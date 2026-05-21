"""Test fixtures.

Tests run against a real Postgres database (the same dev container) so
features like sequences, NUMERIC, and constraints behave identically to
production. Run via:

    docker compose exec app pytest

Each session creates a fresh `traiteurs_test` database, applies all
Alembic migrations, then seeds known users for role-based tests.
"""

import os

import bcrypt
import pyotp
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Known TOTP secret pre-loaded on admin@test.local (see `_seed_users`).
# The `login` fixture below uses it to clear the MFA second-factor gate
# automatically, so existing admin tests don't need to know MFA exists.
# Tests that exercise the un-enrolled path reset this state explicitly
# (see tests/test_mfa.py).
_ADMIN_MFA_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def _ensure_test_db():
    """Drop + recreate the test database from the parent server."""
    parent_url = os.environ.get(
        "DATABASE_URL", "postgresql://traiteurs:traiteurs@db:5432/traiteurs"
    )
    test_db_name = "traiteurs_test"
    # Connect to the 'postgres' maintenance DB so we never hold a connection
    # to traiteurs_test while trying to drop it (CI sets DATABASE_URL to
    # traiteurs_test directly, causing "cannot drop the currently open database").
    maint_url = parent_url.rsplit("/", 1)[0] + "/postgres"
    parent_engine = create_engine(maint_url, isolation_level="AUTOCOMMIT")
    with parent_engine.connect() as conn:
        # Disconnect anyone holding the test DB open before dropping
        conn.execute(
            text(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{test_db_name}' AND pid <> pg_backend_pid()"
            )
        )
        conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name}"))
        conn.execute(text(f"CREATE DATABASE {test_db_name}"))
    parent_engine.dispose()
    test_url = parent_url.rsplit("/", 1)[0] + f"/{test_db_name}"
    return test_url


@pytest.fixture(scope="session", autouse=True)
def _required_env():
    """Provide SECRET_KEY + a clean test DB url before any app import."""
    # `setdefault` is not enough: docker-compose interpolates
    # `${VAR:-}` to an empty string, which leaves the key present in
    # os.environ but unusable. Treat empty as absent.
    if not os.environ.get("SECRET_KEY"):
        os.environ["SECRET_KEY"] = "x" * 32
    test_url = _ensure_test_db()
    os.environ["DATABASE_URL"] = test_url
    os.environ.pop("STRIPE_SECRET_KEY", None)
    # Use the in-memory dramatiq stub broker (no Redis dependency in tests).
    # services/billing_tasks.py reads this at import time.
    os.environ["DRAMATIQ_TESTING"] = "1"
    # The rate-limiter refuses to start on `memory://` outside of dev/test
    # (audit H-3, 2026-05-13). The test suite runs single-process and
    # doesn't exercise Redis itself, so opt-in to the in-memory store
    # explicitly here — that's exactly what `LIMITER_ALLOW_MEMORY` is for.
    if not os.environ.get("LIMITER_ALLOW_MEMORY"):
        os.environ["LIMITER_ALLOW_MEMORY"] = "1"
    # Same for the SESSION_COOKIE_SECURE default: the test client doesn't
    # speak HTTPS, so leaving the Secure flag on means the session cookie
    # never round-trips, breaking every authenticated assertion. Override
    # for tests; prod keeps the safe True default flipped by H-13.
    if not os.environ.get("SECURE_COOKIES"):
        os.environ["SECURE_COOKIES"] = "false"
    yield


@pytest.fixture(scope="session")
def app(_required_env):
    # Late import — config.Settings() runs at import and needs SECRET_KEY/DATABASE_URL.
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
    # Kill the rate limiter for tests — otherwise the 10/min login limit
    # collides with the 23 parametrised logins this suite performs.
    from extensions import limiter

    limiter.enabled = False

    with flask_app.app_context():
        _seed_users()
    yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_users():
    import datetime

    from sqlalchemy import select

    from database import engine
    from models import Caterer, CatererStructureType, Company, User, UserRole
    from services import mfa as mfa_service

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

        # Pre-enroll admin@test.local in MFA so non-MFA tests can `login()`
        # straight to the dashboard. Without this, the new force_mfa_enrollment
        # hook (RGS-AUTH.2) would redirect every admin test to /mfa/setup.
        # MFA-specific tests reset this state explicitly when they need the
        # un-enrolled path.
        admin = s.scalar(select(User).where(User.email == "admin@test.local"))
        admin.mfa_secret = mfa_service.encrypt_secret(_ADMIN_MFA_SECRET)
        admin.mfa_recovery_codes = mfa_service.hash_recovery_codes(
            mfa_service.generate_recovery_codes()
        )
        admin.mfa_enabled = True
        admin.mfa_enrolled_at = datetime.datetime.utcnow()
        s.commit()
    finally:
        s.close()


@pytest.fixture
def login(client):
    """Log `client` in as a known seeded user. CSRF is disabled in tests.

    If the seeded user has MFA enabled (admin@test.local), the partial
    session minted by /login is auto-cleared by POSTing a fresh TOTP code
    derived from the well-known seed secret. Callers stay oblivious — they
    receive a fully-authenticated session as before.
    """

    def _login(email, password="testpass"):
        r = client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=False,
        )
        # MFA gate: /login redirects to /mfa/verify when the user is enrolled.
        # Auto-verify with the seeded secret so existing tests stay unaware.
        if r.status_code == 302 and "/mfa/verify" in (r.headers.get("Location") or ""):
            code = pyotp.TOTP(_ADMIN_MFA_SECRET).now()
            r = client.post(
                "/mfa/verify",
                data={"code": code},
                follow_redirects=False,
            )
        return r

    return _login
