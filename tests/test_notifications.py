import datetime as _dt
import uuid
from decimal import Decimal

import pytest


@pytest.fixture
def session(app):
    from database import session_factory

    s = session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _seed_pending_qr(s, *, n_validated_caterers=2):
    from sqlalchemy import select

    from models import (
        Caterer,
        CatererStructureType,
        Company,
        QuoteRequest,
        QuoteRequestStatus,
        User,
    )

    acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
    alice = s.scalar(select(User).where(User.email == "alice@test.local"))

    caterers = []
    for i in range(n_validated_caterers):
        c = Caterer(
            name=f"Notif Caterer {uuid.uuid4().hex[:6]}",
            siret=f"4{uuid.uuid4().hex[:13]}",
            structure_type=CatererStructureType.ESAT,
            invoice_prefix=f"NTF{i}{uuid.uuid4().hex[:4].upper()}",
            is_validated=True,
        )
        s.add(c)
        s.flush()
        # Each caterer has one user, so notifications can land somewhere.
        s.add(
            User(
                email=f"caterer-{c.id.hex[:8]}@test.local",
                password_hash="x",
                first_name="C",
                last_name="K",
                role=UserRole.caterer,
                caterer_id=c.id,
            )
        )
        caterers.append(c)

    qr = QuoteRequest(
        company_id=acme.id,
        user_id=alice.id,
        guest_count=12,
        status=QuoteRequestStatus.pending_review,
        event_address="1 rue Test",
        event_city="Paris",
        event_zip_code="75001",
        event_date=_dt.date.today() + _dt.timedelta(days=30),
    )
    s.add(qr)
    s.flush()
    return qr.id, [c.id for c in caterers]


# Late import of UserRole to keep the helper above readable.
from models import UserRole  # noqa: E402


def test_notifications_modal_escapes_body_against_xss(client, login):
    from sqlalchemy import select

    from database import session_factory
    from models import Notification, User
    from services.notifications import create_notification

    payload = "<script>window.__pwned=1</script>"

    s = session_factory()
    try:
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))
        create_notification(
            s,
            user_id=alice.id,
            type="test_xss",
            title="XSS test",
            body=payload,
        )
        s.commit()
    finally:
        s.close()

    try:
        login("alice@test.local")
        # The bell modal is injected on every authenticated page via the
        # `_inject_notifications` context processor — any page works.
        r = client.get("/client/dashboard", follow_redirects=False)
        assert r.status_code == 200
        body = r.data.decode("utf-8", errors="replace")
        # Raw script tag must NOT appear unescaped.
        assert payload not in body, (
            "XSS regression: <script> rendered as raw HTML in the bell modal"
        )
        # The escaped form (or some character entity equivalent) MUST appear,
        # proving the body went through the template at all.
        assert "&lt;script&gt;" in body or "&lt;/script&gt;" in body, (
            "expected the notification body to render escaped"
        )
    finally:
        # Clean up: the notification we created persists across tests in
        # this DB session. Drop it so /client/dashboard requests in later
        # tests don't render an unexpected extra row.
        s = session_factory()
        try:
            s.execute(
                Notification.__table__.delete().where(Notification.type == "test_xss")
            )
            s.commit()
        finally:
            s.close()


def _bell_badge_html(body: str) -> str:
    import re

    m = re.search(
        r'<span class="notification-badge[^"]*"[^>]*>.*?</span>',
        body,
        flags=re.DOTALL,
    )
    return m.group(0) if m else ""


@pytest.fixture
def seed_alice_unread():
    from sqlalchemy import select

    from database import session_factory
    from models import Notification, User

    def _seed(n: int):
        s = session_factory()
        try:
            alice = s.scalar(select(User).where(User.email == "alice@test.local"))
            # Wipe first so the badge count reflects exactly `n`, not
            # whatever leaked from prior tests sharing the DB.
            s.execute(
                Notification.__table__.delete().where(Notification.user_id == alice.id)
            )
            if n > 0:
                s.execute(
                    Notification.__table__.insert(),
                    [
                        {
                            "user_id": alice.id,
                            "type": "test_bell_badge",
                            "title": f"badge test {i}",
                            "body": None,
                            "is_read": False,
                        }
                        for i in range(n)
                    ],
                )
            s.commit()
            return alice.id
        finally:
            s.close()

    yield _seed

    s = session_factory()
    try:
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))
        s.execute(
            Notification.__table__.delete().where(Notification.user_id == alice.id)
        )
        s.commit()
    finally:
        s.close()


@pytest.mark.parametrize(
    "n, expect_hidden, expect_text",
    [
        # Zero unread: badge present in HTML but carries `hidden` —
        # the previous shape left this to a JS fetch, so a single
        # fetch hiccup blinded users to fresh notifs.
        (0, True, "0"),
        # Typical case: badge visible with the literal count.
        (3, False, "3"),
        # Clamp: past 99 we collapse to "99+" so the pill width stays
        # bounded on a 36px button. `data-count` keeps the truth.
        (120, False, "99+"),
    ],
    ids=["zero", "three", "clamped"],
)
def test_bell_badge_renders_unread_count(
    client, login, seed_alice_unread, n, expect_hidden, expect_text
):
    seed_alice_unread(n)
    login("alice@test.local")
    r = client.get("/client/dashboard")
    assert r.status_code == 200

    badge = _bell_badge_html(r.data.decode("utf-8", errors="replace"))
    assert badge, "bell badge must always be rendered, even at zero count"

    has_hidden = "hidden" in badge
    assert has_hidden is expect_hidden, (
        f"badge `hidden` mismatch for n={n}: got {badge}"
    )
    assert f'data-count="{n}"' in badge
    assert f">{expect_text}<" in badge.replace(" ", "").replace("\n", ""), (
        f"badge text mismatch for n={n}: got {badge}"
    )


def test_approve_notifies_every_validated_caterer(session):
    from sqlalchemy import select

    from models import Caterer, Notification, User
    from services import workflow

    qr_id, my_caterer_ids = _seed_pending_qr(session, n_validated_caterers=3)
    # The conftest also seeds a Test Caterer that's already validated;
    # the fallback set is "every validated caterer", so include those.
    all_validated_ids = set(
        session.scalars(select(Caterer.id).where(Caterer.is_validated.is_(True)))
    )
    expected_user_ids = set(
        session.scalars(select(User.id).where(User.caterer_id.in_(all_validated_ids)))
    )

    workflow.approve_quote_request(session, request_id=qr_id)
    session.flush()

    notified_caterer_user_ids = set(
        session.scalars(
            select(Notification.user_id).where(
                Notification.type == "quote_request_received",
                Notification.related_entity_id == qr_id,
            )
        )
    )
    # My seeded caterers must be in the notified set. The full equality
    # check covers any other validated caterers that might have leaked
    # from prior tests in the run.
    for cid in my_caterer_ids:
        assert cid in all_validated_ids
    assert notified_caterer_user_ids == expected_user_ids, (
        "every validated caterer must receive a notification — no caterer "
        "in the catalog should be silently skipped"
    )


def test_approve_notifies_requester_with_target_count(session):
    from sqlalchemy import func, select

    from models import Notification, QuoteRequest, QuoteRequestCaterer
    from services import workflow

    qr_id, _ = _seed_pending_qr(session, n_validated_caterers=2)
    workflow.approve_quote_request(session, request_id=qr_id)
    session.flush()

    actual_qrcs = session.scalar(
        select(func.count(QuoteRequestCaterer.id)).where(
            QuoteRequestCaterer.quote_request_id == qr_id,
        )
    )
    qr = session.get(QuoteRequest, qr_id)
    requester_notif = session.scalar(
        select(Notification).where(
            Notification.user_id == qr.user_id,
            Notification.type == "quote_request_approved",
            Notification.related_entity_id == qr_id,
        )
    )
    assert requester_notif is not None
    assert f"{actual_qrcs} traiteur" in requester_notif.body, (
        f"expected '{actual_qrcs} traiteur' in body, got {requester_notif.body!r}"
    )


def test_approve_skips_notifications_when_no_validated_caterer(session):
    from sqlalchemy import select

    from models import Caterer, Notification, QuoteRequest, QuoteRequestStatus
    from services import workflow

    # Invalidate every existing validated caterer so the fallback set is
    # also empty.
    for c in session.scalars(
        select(Caterer).where(Caterer.is_validated.is_(True))
    ).all():
        c.is_validated = False
    session.flush()

    qr_id, _ = _seed_pending_qr(session, n_validated_caterers=0)
    workflow.approve_quote_request(session, request_id=qr_id)
    session.flush()

    qr = session.get(QuoteRequest, qr_id)
    assert qr.status == QuoteRequestStatus.pending_review, (
        "with no validated caterer, the demand must NOT flip to sent_to_caterers"
    )
    notif_count = session.scalar(
        select(__import__("sqlalchemy").func.count(Notification.id)).where(
            Notification.related_entity_id == qr_id,
        )
    )
    assert notif_count == 0, "no caterers reached → no notifications should fire"


def _seed_qr_ready_for_submit(session, *, n_caterers, prior_transmitted=0):
    from sqlalchemy import select

    from models import (
        Caterer,
        CatererStructureType,
        Company,
        QRCStatus,
        Quote,
        QuoteRequest,
        QuoteRequestCaterer,
        QuoteRequestStatus,
        QuoteStatus,
        User,
    )

    acme = session.scalar(select(Company).where(Company.siret == "12345678901234"))
    alice = session.scalar(select(User).where(User.email == "alice@test.local"))

    qr = QuoteRequest(
        company_id=acme.id,
        user_id=alice.id,
        guest_count=10,
        status=QuoteRequestStatus.sent_to_caterers,
        event_address="1 rue Test",
        event_city="Paris",
        event_zip_code="75001",
        event_date=_dt.date.today() + _dt.timedelta(days=30),
    )
    session.add(qr)
    session.flush()

    caterers = []
    quote_ids = []
    for i in range(n_caterers):
        c = Caterer(
            name=f"Submit Caterer {uuid.uuid4().hex[:6]}",
            siret=f"5{uuid.uuid4().hex[:13]}",
            structure_type=CatererStructureType.ESAT,
            invoice_prefix=f"SBM{i}{uuid.uuid4().hex[:4].upper()}",
            is_validated=True,
        )
        session.add(c)
        session.flush()
        qrc_status = (
            QRCStatus.transmitted_to_client
            if i < prior_transmitted
            else QRCStatus.selected
        )
        qrc = QuoteRequestCaterer(
            quote_request_id=qr.id,
            caterer_id=c.id,
            status=qrc_status,
        )
        if qrc_status == QRCStatus.transmitted_to_client:
            qrc.response_rank = i + 1
            qrc.responded_at = _dt.datetime.utcnow()
        session.add(qrc)
        quote = Quote(
            quote_request_id=qr.id,
            caterer_id=c.id,
            reference=f"DEVIS-NTF-{uuid.uuid4().hex[:6].upper()}",
            total_amount_ht=Decimal("100"),
            status=QuoteStatus.draft,
        )
        session.add(quote)
        session.flush()
        caterers.append(c)
        quote_ids.append(quote.id)
    return qr.id, caterers, quote_ids


def test_submit_quote_notifies_requester_when_transmitted(session):
    from sqlalchemy import select

    from models import Notification, QuoteRequest
    from services import workflow

    qr_id, caterers, quote_ids = _seed_qr_ready_for_submit(
        session, n_caterers=3, prior_transmitted=0
    )
    workflow.submit_quote(
        session,
        request_id=qr_id,
        quote_id=quote_ids[0],
        caterer=caterers[0],
    )
    session.flush()

    qr = session.get(QuoteRequest, qr_id)
    notif = session.scalar(
        select(Notification).where(
            Notification.user_id == qr.user_id,
            Notification.type == "quote_received",
        )
    )
    assert notif is not None, "transmitted quote must notify the requester"


def test_submit_quote_does_not_notify_on_4th_responder(session):
    from sqlalchemy import func, select

    from models import Notification, QuoteRequest
    from services import workflow

    qr_id, caterers, quote_ids = _seed_qr_ready_for_submit(
        session, n_caterers=4, prior_transmitted=3
    )
    qr = session.get(QuoteRequest, qr_id)
    before = session.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == qr.user_id,
            Notification.type == "quote_received",
        )
    )

    with pytest.raises(workflow.QuoteRequestClosed):
        workflow.submit_quote(
            session,
            request_id=qr_id,
            quote_id=quote_ids[3],
            caterer=caterers[3],
        )
    session.flush()

    after = session.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == qr.user_id,
            Notification.type == "quote_received",
        )
    )
    assert after == before, "closed-out 4th submit must not notify the requester"


def _seed_qr_for_alice(s):
    from sqlalchemy import select

    from models import Company, QuoteRequest, QuoteRequestStatus, User

    acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
    alice = s.scalar(select(User).where(User.email == "alice@test.local"))
    qr = QuoteRequest(
        company_id=acme.id,
        user_id=alice.id,
        guest_count=10,
        status=QuoteRequestStatus.sent_to_caterers,
        event_address="1 rue Test",
        event_city="Paris",
        event_zip_code="75001",
        event_date=_dt.date.today() + _dt.timedelta(days=21),
    )
    s.add(qr)
    s.flush()
    return qr.id


def test_visiting_request_detail_marks_quote_request_notifs_read(client, login):
    from sqlalchemy import select

    from database import session_factory
    from models import Notification, User
    from services.notifications import create_notification

    s = session_factory()
    try:
        qr_id = _seed_qr_for_alice(s)
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))
        create_notification(
            s,
            user_id=alice.id,
            type="quote_request_approved",
            title="Demande approuvée",
            body="Test",
            related_entity_type="quote_request",
            related_entity_id=qr_id,
        )
        s.commit()
        notif_id = s.scalar(
            select(Notification.id).where(
                Notification.user_id == alice.id,
                Notification.related_entity_id == qr_id,
            )
        )
    finally:
        s.close()

    try:
        login("alice@test.local")
        r = client.get(f"/client/requests/{qr_id}", follow_redirects=False)
        assert r.status_code == 200

        s = session_factory()
        try:
            notif = s.get(Notification, notif_id)
            assert notif.is_read is True, (
                "viewing the request detail page must mark its notif read"
            )
        finally:
            s.close()
    finally:
        s = session_factory()
        try:
            s.execute(
                Notification.__table__.delete().where(Notification.id == notif_id)
            )
            s.commit()
        finally:
            s.close()


def test_visiting_request_detail_also_clears_child_quote_notifs(client, login):
    from decimal import Decimal as _D

    from sqlalchemy import select

    from database import session_factory
    from models import (
        Caterer,
        Notification,
        Quote,
        QuoteRequestCaterer,
        QuoteStatus,
        User,
    )
    from services.notifications import create_notification

    s = session_factory()
    try:
        qr_id = _seed_qr_for_alice(s)
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))
        caterer = s.scalar(select(Caterer).where(Caterer.siret == "98765432109876"))
        # A quote needs a parent QRC row (QuoteRequestCaterer) for the
        # request_detail page to render its listing block.
        qrc = QuoteRequestCaterer(
            quote_request_id=qr_id,
            caterer_id=caterer.id,
        )
        s.add(qrc)
        s.flush()
        quote = Quote(
            quote_request_id=qr_id,
            caterer_id=caterer.id,
            reference=f"TST-{uuid.uuid4().hex[:6].upper()}",
            status=QuoteStatus.sent,
            total_amount_ht=_D("100.00"),
        )
        s.add(quote)
        s.flush()
        create_notification(
            s,
            user_id=alice.id,
            type="quote_received",
            title="Devis reçu",
            body="Test",
            related_entity_type="quote",
            related_entity_id=quote.id,
        )
        s.commit()
        notif_id = s.scalar(
            select(Notification.id).where(
                Notification.user_id == alice.id,
                Notification.related_entity_id == quote.id,
            )
        )
        quote_id = quote.id
    finally:
        s.close()

    try:
        login("alice@test.local")
        r = client.get(f"/client/requests/{qr_id}", follow_redirects=False)
        assert r.status_code == 200

        s = session_factory()
        try:
            notif = s.get(Notification, notif_id)
            assert notif.is_read is True, (
                "child quote notif must be cleared when the parent QR page is viewed"
            )
        finally:
            s.close()
    finally:
        s = session_factory()
        try:
            s.execute(
                Notification.__table__.delete().where(Notification.id == notif_id)
            )
            s.execute(Quote.__table__.delete().where(Quote.id == quote_id))
            s.commit()
        finally:
            s.close()


def test_post_to_detail_does_not_mark_read(client, login):
    from sqlalchemy import select

    from database import session_factory
    from models import Notification, User
    from services.notifications import create_notification

    s = session_factory()
    try:
        qr_id = _seed_qr_for_alice(s)
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))
        create_notification(
            s,
            user_id=alice.id,
            type="quote_request_approved",
            title="Demande approuvée",
            body="Test",
            related_entity_type="quote_request",
            related_entity_id=qr_id,
        )
        s.commit()
        notif_id = s.scalar(
            select(Notification.id).where(
                Notification.user_id == alice.id,
                Notification.related_entity_id == qr_id,
            )
        )
    finally:
        s.close()

    try:
        login("alice@test.local")
        # POST to a non-existent endpoint just to assert the hook's
        # GET-only contract — we only care that the notif stays unread,
        # not what the POST returns.
        client.post(f"/client/requests/{qr_id}", data={})

        s = session_factory()
        try:
            notif = s.get(Notification, notif_id)
            assert notif.is_read is False, (
                "POST to a detail URL must NOT auto-mark notifs read"
            )
        finally:
            s.close()
    finally:
        s = session_factory()
        try:
            s.execute(
                Notification.__table__.delete().where(Notification.id == notif_id)
            )
            s.commit()
        finally:
            s.close()


def test_visiting_dashboard_marks_company_notifs_read(client, login):
    from sqlalchemy import select

    from database import session_factory
    from models import Notification, User
    from services.notifications import create_notification

    s = session_factory()
    try:
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))
        create_notification(
            s,
            user_id=alice.id,
            type="membership_approved",
            title="Bienvenue !",
            body="Test",
            related_entity_type="company",
            related_entity_id=alice.company_id,
        )
        s.commit()
        notif_id = s.scalar(
            select(Notification.id).where(
                Notification.user_id == alice.id,
                Notification.related_entity_type == "company",
                Notification.is_read.is_(False),
            )
        )
    finally:
        s.close()

    try:
        login("alice@test.local")
        r = client.get("/client/dashboard", follow_redirects=False)
        assert r.status_code == 200

        s = session_factory()
        try:
            notif = s.get(Notification, notif_id)
            assert notif.is_read is True, (
                "viewing the dashboard must clear company-type notifs"
            )
        finally:
            s.close()
    finally:
        s = session_factory()
        try:
            s.execute(
                Notification.__table__.delete().where(Notification.id == notif_id)
            )
            s.commit()
        finally:
            s.close()
