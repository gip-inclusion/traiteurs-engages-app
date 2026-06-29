"""Annulation admin d'une demande de devis déjà envoyée aux traiteurs."""

from __future__ import annotations

import datetime as _dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select


@pytest.fixture
def captured_emails(monkeypatch):
    calls = []

    def _record(**kwargs):
        calls.append(kwargs)

    from services import email as email_module

    monkeypatch.setattr(email_module.send_email_async, "send", _record)
    return calls


def _make_caterer_with_user(s):
    from models import Caterer, CatererStructureType, User, UserRole

    suffix = uuid.uuid4().hex[:6]
    c = Caterer(
        name=f"Cancel-Cat-{suffix}",
        siret=f"77{uuid.uuid4().int % 10**12:012d}"[:14],
        structure_type=CatererStructureType.ESAT,
        invoice_prefix=f"C{suffix[:5]}",
        is_validated=True,
    )
    s.add(c)
    s.flush()
    u = User(
        email=f"cat-{suffix}@test.local",
        password_hash="x",
        first_name="Cat",
        last_name="X",
        role=UserRole.caterer,
        caterer_id=c.id,
    )
    s.add(u)
    s.flush()
    return c, u


def _seed_sent_request(s, *, caterer=None, with_sent_quote=False, status=None):
    from models import (
        Company,
        MealType,
        QRCStatus,
        Quote,
        QuoteRequest,
        QuoteRequestCaterer,
        QuoteRequestStatus,
        QuoteStatus,
        User,
    )

    acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
    alice = s.scalar(select(User).where(User.email == "alice@test.local"))
    qr = QuoteRequest(
        company_id=acme.id,
        user_id=alice.id,
        meal_type=MealType.plateaux_repas,
        event_date=_dt.date.today() + _dt.timedelta(days=30),
        guest_count=12,
        event_city="Paris",
        status=status or QuoteRequestStatus.sent_to_caterers,
        is_compare_mode=True,
    )
    s.add(qr)
    s.flush()
    quote_id = None
    if caterer is not None:
        s.add(
            QuoteRequestCaterer(
                quote_request_id=qr.id,
                caterer_id=caterer.id,
                status=QRCStatus.transmitted_to_client
                if with_sent_quote
                else QRCStatus.selected,
            )
        )
        if with_sent_quote:
            q = Quote(
                quote_request_id=qr.id,
                caterer_id=caterer.id,
                reference=f"DEVIS-{uuid.uuid4().hex[:8]}",
                total_amount_ht=Decimal("250"),
                status=QuoteStatus.sent,
            )
            s.add(q)
            s.flush()
            quote_id = q.id
        s.flush()
    return qr.id, quote_id


def _cleanup(qr_id=None, caterer_id=None, user_id=None):
    from database import session_factory
    from models import (
        Caterer,
        Notification,
        Quote,
        QuoteRequest,
        QuoteRequestCaterer,
        User,
    )

    s = session_factory()
    try:
        if qr_id is not None:
            s.execute(
                Notification.__table__.delete().where(
                    Notification.related_entity_id == qr_id
                )
            )
            s.execute(Quote.__table__.delete().where(Quote.quote_request_id == qr_id))
            s.execute(
                QuoteRequestCaterer.__table__.delete().where(
                    QuoteRequestCaterer.quote_request_id == qr_id
                )
            )
            s.execute(QuoteRequest.__table__.delete().where(QuoteRequest.id == qr_id))
        if user_id is not None:
            s.execute(User.__table__.delete().where(User.id == user_id))
        if caterer_id is not None:
            s.execute(Caterer.__table__.delete().where(Caterer.id == caterer_id))
        s.commit()
    finally:
        s.close()


# ---------------------------------------------------------------------------


def test_cancel_sent_request_closes_everything_and_notifies(
    client, login, captured_emails
):
    from database import session_factory
    from models import (
        Notification,
        QRCStatus,
        Quote,
        QuoteRequest,
        QuoteRequestCaterer,
        QuoteRequestStatus,
        QuoteStatus,
        User,
    )

    s = session_factory()
    try:
        caterer, cat_user = _make_caterer_with_user(s)
        caterer_id, user_id = caterer.id, cat_user.id
        cat_user_email = cat_user.email
        qr_id, quote_id = _seed_sent_request(s, caterer=caterer, with_sent_quote=True)
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/qualification/{qr_id}/cancel",
            data={"rejection_reason": "Événement annulé par le client."},
            follow_redirects=False,
        )
        assert r.status_code == 302
        s2 = session_factory()
        try:
            qr = s2.get(QuoteRequest, qr_id)
            assert qr.status == QuoteRequestStatus.cancelled
            assert qr.cancellation_reason == "Événement annulé par le client."
            qrc = s2.scalar(
                select(QuoteRequestCaterer).where(
                    QuoteRequestCaterer.quote_request_id == qr_id
                )
            )
            assert qrc.status == QRCStatus.closed
            quote = s2.get(Quote, quote_id)
            assert quote.status == QuoteStatus.refused
            # Notif traiteur + notif client
            caterer_notif = s2.scalar(
                select(Notification).where(
                    Notification.user_id == user_id,
                    Notification.type == "quote_request_cancelled",
                )
            )
            assert caterer_notif is not None
            alice = s2.scalar(select(User).where(User.email == "alice@test.local"))
            client_notif = s2.scalar(
                select(Notification).where(
                    Notification.user_id == alice.id,
                    Notification.type == "quote_request_cancelled",
                    Notification.related_entity_id == qr_id,
                )
            )
            assert client_notif is not None
        finally:
            s2.close()
        # Email d'annulation envoyé au traiteur
        assert len(captured_emails) == 1
        assert captured_emails[0]["to"] == cat_user_email
        assert "annul" in captured_emails[0]["subject"].lower()
    finally:
        _cleanup(qr_id=qr_id, caterer_id=caterer_id, user_id=user_id)


def test_cancel_blocked_for_non_sent_status(client, login):
    from database import session_factory
    from models import QuoteRequest, QuoteRequestStatus

    s = session_factory()
    try:
        qr_id, _ = _seed_sent_request(s, status=QuoteRequestStatus.completed)
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/qualification/{qr_id}/cancel",
            data={},
            follow_redirects=False,
        )
        # Statut non annulable → redirection détail, demande inchangée
        assert r.status_code == 302
        s2 = session_factory()
        try:
            assert s2.get(QuoteRequest, qr_id).status == QuoteRequestStatus.completed
        finally:
            s2.close()
    finally:
        _cleanup(qr_id=qr_id)


def test_cancel_forbidden_for_non_admin(client, login):
    from database import session_factory

    s = session_factory()
    try:
        qr_id, _ = _seed_sent_request(s)
        s.commit()
    finally:
        s.close()
    try:
        login("cook@test.local")  # role caterer
        r = client.post(f"/admin/qualification/{qr_id}/cancel", data={})
        assert r.status_code == 403
    finally:
        _cleanup(qr_id=qr_id)


def test_cancellation_reason_visible_to_client(client, login):
    from database import session_factory
    from models import QuoteRequest, QuoteRequestStatus

    s = session_factory()
    try:
        qr_id, _ = _seed_sent_request(s, status=QuoteRequestStatus.cancelled)
        qr = s.get(QuoteRequest, qr_id)
        qr.cancellation_reason = "Budget non validé en interne."
        s.commit()
    finally:
        s.close()
    try:
        # alice est cliente admin de la société propriétaire (ACME)
        login("alice@test.local")
        r = client.get(f"/client/requests/{qr_id}")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "Demande annulée" in body
        assert "Budget non validé en interne." in body
    finally:
        _cleanup(qr_id=qr_id)
