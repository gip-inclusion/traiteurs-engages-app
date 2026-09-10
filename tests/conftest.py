import os

import bcrypt
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

_TEST_DB_NAME = "traiteurs_test"


def _db_urls():
    parent_url = os.environ.get(
        "DATABASE_URL", "postgresql://traiteurs:traiteurs@db:5432/traiteurs"
    )
    base = parent_url.rsplit("/", 1)[0]
    return base + "/postgres", base + f"/{_TEST_DB_NAME}"


# L'environnement de test est posé à l'import de conftest, pas dans une
# fixture : des modules de test importent du code applicatif dès la collecte
# (services.email → config + broker dramatiq), donc avant qu'aucune fixture
# n'ait tourné. config.settings est figé à ce moment-là, et sans REDIS_URL la
# construction du broker lève (CI). Les valeurs doivent donc être en place
# avant le premier import de test.
if not os.environ.get("SECRET_KEY"):
    os.environ["SECRET_KEY"] = "x" * 32
os.environ["DATABASE_URL"] = _db_urls()[1]
os.environ["DRAMATIQ_TESTING"] = "1"
os.environ.pop("STRIPE_SECRET_KEY", None)
if not os.environ.get("LIMITER_ALLOW_MEMORY"):
    os.environ["LIMITER_ALLOW_MEMORY"] = "1"
if not os.environ.get("SECURE_COOKIES"):
    os.environ["SECURE_COOKIES"] = "false"


def _ensure_test_db():
    maint_url, _ = _db_urls()
    parent_engine = create_engine(maint_url, isolation_level="AUTOCOMMIT")
    with parent_engine.connect() as conn:
        conn.execute(
            text(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{_TEST_DB_NAME}' AND pid <> pg_backend_pid()"
            )
        )
        conn.execute(text(f"DROP DATABASE IF EXISTS {_TEST_DB_NAME}"))
        conn.execute(text(f"CREATE DATABASE {_TEST_DB_NAME}"))
    parent_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _required_env():
    _ensure_test_db()
    yield


@pytest.fixture(scope="session")
def app(_required_env):
    from alembic import command
    from alembic.config import Config as AlembicConfig

    alembic_cfg = AlembicConfig("alembic.ini")
    command.upgrade(alembic_cfg, "head")

    from app import create_app

    flask_app = create_app()
    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
    )
    from extensions import limiter

    limiter.enabled = False

    with flask_app.app_context():
        _seed_users()
    yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_users():
    from database import engine
    from models import Caterer, CatererStructureType, Company, User, UserRole

    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        if s.query(User).count() > 0:
            return
        pwhash = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
        company = Company(name="ACME Test", siret="12345678901234")
        s.add(company)
        s.flush()
        caterer = Caterer(
            name="Test Caterer",
            siret="98765432109876",
            structure_type=CatererStructureType.ESAT,
            invoice_prefix="TST",
            is_validated=True,
        )
        s.add(caterer)
        s.flush()
        s.add_all(
            [
                User(
                    email="admin@test.local",
                    password_hash=pwhash,
                    first_name="A",
                    last_name="A",
                    role=UserRole.super_admin,
                ),
                User(
                    email="alice@test.local",
                    password_hash=pwhash,
                    first_name="A",
                    last_name="L",
                    role=UserRole.client_admin,
                    company_id=company.id,
                ),
                User(
                    email="bob@test.local",
                    password_hash=pwhash,
                    first_name="B",
                    last_name="B",
                    role=UserRole.client_user,
                    company_id=company.id,
                ),
                User(
                    email="cook@test.local",
                    password_hash=pwhash,
                    first_name="C",
                    last_name="K",
                    role=UserRole.caterer,
                    caterer_id=caterer.id,
                ),
            ]
        )
        s.commit()
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _stub_geocoder(monkeypatch):
    """Coupe le réseau pour Nominatim dans tous les tests.

    Les hooks de géocodage (profil traiteur, création de demande, fan-out)
    appellent services.geocoding.geocode_address. Par défaut on renvoie
    None pour que les tests qui ne s'intéressent pas au géocodage ne
    soient pas dépendants du réseau ni ralentis. Les tests qui veulent
    vérifier la persistance des coordonnées re-monkeypatchent localement.
    """
    from services import geocoding

    monkeypatch.setattr(geocoding, "geocode_address", lambda *a, **kw: None)


@pytest.fixture
def login(client):
    def _login(email, password="testpass"):
        resp = client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=False,
        )
        assert resp.status_code == 302, (
            f"login({email!r}) failed: status={resp.status_code} body={resp.data!r}"
        )
        return resp

    return _login
