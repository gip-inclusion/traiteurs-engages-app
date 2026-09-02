import html as _html
import re
import uuid

import pytest

from tests import test_email_triggers as seeds


# Familles de délimiteurs de moteurs de template. Brevo évalue au moins la
# famille Twig et les merge tags historiques Sendinblue ; le correctif ne
# doit dépendre d'aucune de ces syntaxes en particulier. Ajouter une famille
# ici doit rester une seule ligne.
PAYLOADS = [
    "{{7*7}}",
    "{% raw %}x{% endraw %}",
    "{# commentaire #}",
    "%FIRSTNAME%",
    "${7*7}",
    "[[7*7]]",
    "<%= 7*7 %>",
    "{FIRSTNAME}",
    "{{{{7*7}}}}",
]

WORD_JOINER = "⁠"

# Sentinelles purement alphanumériques : elles traversent le durcissement
# intactes, ce qui permet de découper la région issue de la donnée
# utilisateur dans le contenu final.
OPEN = "SENTINELOPEN"
CLOSE = "SENTINELCLOSE"

# Une région durcie en HTML ne contient plus que des alphanumériques, des
# blancs, des entités numériques et du non-ASCII.
_HTML_INERT_RE = re.compile(r"(?:[A-Za-z0-9\s]|&#\d+;|[^\x00-\x7f])*")


def wrap(payload):
    return f"{OPEN}{payload}{CLOSE}"


@pytest.fixture
def session(app):
    from database import session_factory

    s = session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def brevo_payloads(monkeypatch):
    """Capture ce qui part réellement chez Brevo.

    Le mock de tests/test_email_triggers.py intercepte à
    `send_email_async.send`, donc en amont du sink : il ne prouve rien sur
    le payload transmis. Ici on court-circuite l'acteur dramatiq (StubBroker
    en test : `.send()` n'exécute rien) et on capture le dict final.
    """
    sent = []

    import config
    from services import email as email_module

    monkeypatch.setattr(config, "BREVO_API_KEY", "test-key")
    monkeypatch.setattr(
        email_module,
        "_post_to_brevo",
        lambda payload, api_key, **kw: sent.append(payload),
    )
    monkeypatch.setattr(
        email_module.send_email_async,
        "send",
        lambda **kw: email_module.send_email_sync(**kw),
    )
    return sent


def _region(rendered):
    assert OPEN in rendered, f"sentinelle ouvrante absente de {rendered[:200]!r}"
    assert CLOSE in rendered, f"sentinelle fermante absente de {rendered[:200]!r}"
    return rendered.split(OPEN, 1)[1].split(CLOSE, 1)[0]


def assert_html_inert(rendered, payload):
    region = _region(rendered)
    assert _HTML_INERT_RE.fullmatch(region), f"métacaractère brut dans {region!r}"
    assert _html.unescape(region) == payload, "contenu altéré, pas seulement neutralisé"


def assert_text_inert(rendered, payload):
    region = _region(rendered)
    assert region.replace(WORD_JOINER, "") == payload, "contenu altéré"
    for i, char in enumerate(region):
        if char.isascii() and not char.isalnum() and not char.isspace():
            assert region[i + 1 : i + 2] == WORD_JOINER, (
                f"{char!r} (offset {i}) n'est pas neutralisé dans {region!r}"
            )


def assert_inert(payloads, payload, *, count=1):
    assert len(payloads) == count, (
        f"{count} email(s) attendu(s), {len(payloads)} reçu(s)"
    )
    for sent in payloads:
        assert_html_inert(sent["htmlContent"], payload)
        assert_text_inert(sent["textContent"], payload)


# ---------------------------------------------------------------------------
# Propriété, sur chaque trigger x chaque famille de délimiteurs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", PAYLOADS)
def test_welcome_signup_first_name(app, session, brevo_payloads, payload):
    from services import email_triggers

    alice = seeds._alice(session)
    alice.first_name = wrap(payload)
    session.flush()
    with app.app_context():
        email_triggers.welcome_signup(
            alice, role_kind="client", cta_path="/client/settings"
        )
    assert_inert(brevo_payloads, payload)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_quote_received_caterer_name(app, session, brevo_payloads, payload):
    from services import email_triggers

    quote, caterer = seeds._seed_transmitted_quote(session)
    caterer.name = wrap(payload)
    session.flush()
    with app.app_context():
        email_triggers.quote_received(session, quote=quote, caterer=caterer)
    assert_inert(brevo_payloads, payload)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_order_confirmed_delivery_address(app, session, brevo_payloads, payload):
    from services import email_triggers

    order, caterer, _user = seeds._seed_order_for_email(session)
    order.delivery_address = wrap(payload)
    session.flush()
    with app.app_context():
        email_triggers.order_confirmed(session, order=order)
    assert_inert(brevo_payloads, payload)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_order_confirmed_company_name(app, session, brevo_payloads, payload):
    from services import email_triggers

    from sqlalchemy import select

    from models import Company

    order, caterer, _user = seeds._seed_order_for_email(session)
    acme = session.scalar(select(Company).where(Company.siret == "12345678901234"))
    acme.name = wrap(payload)
    session.flush()
    with app.app_context():
        email_triggers.order_confirmed(session, order=order)
    assert_inert(brevo_payloads, payload)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_quote_request_received_event_city(app, session, brevo_payloads, payload):
    from services import email_triggers

    qr, caterer, _users = seeds._seed_qr_for_quote_request_received(session)
    qr.event_city = wrap(payload)
    session.flush()
    with app.app_context():
        email_triggers.quote_request_received(
            session, quote_request=qr, caterer=caterer
        )
    assert_inert(brevo_payloads, payload)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_quote_request_updated_company_name(app, session, brevo_payloads, payload):
    from services import email_triggers

    qr, caterer, _users = seeds._seed_qr_for_quote_request_received(session)
    qr.company.name = wrap(payload)
    session.flush()
    with app.app_context():
        email_triggers.quote_request_updated(session, quote_request=qr, caterer=caterer)
    assert_inert(brevo_payloads, payload)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_quote_request_cancelled_reason(app, session, brevo_payloads, payload):
    from services import email_triggers

    qr, caterer, _users = seeds._seed_qr_for_quote_request_received(session)
    with app.app_context():
        email_triggers.quote_request_cancelled(
            session, quote_request=qr, caterer=caterer, reason=wrap(payload)
        )
    assert_inert(brevo_payloads, payload)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_message_received_body(app, session, brevo_payloads, payload):
    from services import email_triggers

    alice = seeds._alice(session)
    cook = seeds._cook(session)
    with app.app_context():
        email_triggers.message_received(
            message=seeds._msg(body=wrap(payload)), sender=alice, recipient=cook
        )
    assert_inert(brevo_payloads, payload)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_message_received_sender_name(app, session, brevo_payloads, payload):
    from services import email_triggers

    alice = seeds._alice(session)
    cook = seeds._cook(session)
    alice.first_name = wrap(payload)
    alice.last_name = ""
    session.flush()
    with app.app_context():
        email_triggers.message_received(
            message=seeds._msg(), sender=alice, recipient=cook
        )
    assert_inert(brevo_payloads, payload)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_password_reset_first_name(app, session, brevo_payloads, payload):
    from services import password_reset

    alice = seeds._alice(session)
    alice.first_name = wrap(payload)
    session.flush()
    with app.app_context():
        password_reset.kick_off_reset(session, email=alice.email)
    assert_inert(brevo_payloads, payload)


# ---------------------------------------------------------------------------
# Bout en bout — les deux vecteurs du rapport YWH-PGM43799-3
# ---------------------------------------------------------------------------


def _wipe_signup(email, siret):
    from database import session_factory
    from models import Company, CompanyEmployee, CompanyService, User
    from sqlalchemy import select

    s = session_factory()
    try:
        user_ids = list(s.scalars(select(User.id).where(User.email == email)))
        company_ids = list(s.scalars(select(Company.id).where(Company.siret == siret)))
        if user_ids:
            s.execute(
                CompanyEmployee.__table__.delete().where(
                    CompanyEmployee.user_id.in_(user_ids)
                )
            )
        if company_ids:
            s.execute(
                CompanyService.__table__.delete().where(
                    CompanyService.company_id.in_(company_ids)
                )
            )
            s.execute(
                CompanyEmployee.__table__.delete().where(
                    CompanyEmployee.company_id.in_(company_ids)
                )
            )
        s.execute(User.__table__.delete().where(User.email == email))
        s.execute(Company.__table__.delete().where(Company.siret == siret))
        s.commit()
    finally:
        s.close()


def test_signup_first_name_is_stored_literally_and_sent_inert(
    app, client, session, brevo_payloads
):
    from sqlalchemy import select

    from models import User

    payload = "{{7*7}}"
    email = f"inj-{uuid.uuid4().hex[:8]}@test.local"
    siret = f"9998{uuid.uuid4().int % 10**10:010d}"
    try:
        resp = client.post(
            "/signup",
            data={
                "role": "client_admin",
                "email": email,
                "password": "VeryStrongPw1!",
                "first_name": wrap(payload),
                "last_name": "Test",
                "siret": siret,
                "accept_terms": "on",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303), resp.data[:400]

        created = session.scalar(select(User).where(User.email == email))
        assert created is not None, "compte non créé"
        # Le correctif ne touche pas au stockage : la base garde la chaîne brute.
        assert created.first_name == wrap(payload)

        assert_inert(brevo_payloads, payload)
    finally:
        _wipe_signup(email, siret)


def test_subject_is_hardened_at_the_sink(app, brevo_payloads):
    from services import email as email_module

    payload = "{{7*7}}"
    with app.app_context():
        email_module.send_email_sync(
            to="someone@test.local",
            subject=wrap(payload),
            html=f"<p>{wrap(payload)}</p>",
            text=wrap(payload),
        )
    assert len(brevo_payloads) == 1
    sent = brevo_payloads[0]
    assert "{{" not in sent["subject"]
    assert "{{" not in sent["htmlContent"]
    assert "{{" not in sent["textContent"]
