"""Édition admin d'une demande de devis, y compris après envoi aux
traiteurs (avec re-notification des traiteurs encore en lice)."""

from __future__ import annotations

import datetime as _dt
import uuid

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
        name=f"Edit-Cat-{suffix}",
        siret=f"77{uuid.uuid4().int % 10**12:012d}"[:14],
        structure_type=CatererStructureType.ESAT,
        invoice_prefix=f"E{suffix[:5]}",
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


def _seed_request(s, *, status, target_caterer=None):
    from models import (
        Company,
        MealType,
        QRCStatus,
        QuoteRequest,
        QuoteRequestCaterer,
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
        status=status,
        is_compare_mode=True,
    )
    s.add(qr)
    s.flush()
    if target_caterer is not None:
        s.add(
            QuoteRequestCaterer(
                quote_request_id=qr.id,
                caterer_id=target_caterer.id,
                status=QRCStatus.selected,
            )
        )
        s.flush()
    return qr.id


def _cleanup(qr_id=None, caterer_id=None, user_id=None):
    from database import session_factory
    from models import (
        Caterer,
        Notification,
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


def test_edit_sent_request_updates_fields_and_notifies_caterers(
    client, login, captured_emails
):
    from database import session_factory
    from models import Notification, QuoteRequest, QuoteRequestStatus

    s = session_factory()
    try:
        caterer, cat_user = _make_caterer_with_user(s)
        caterer_id, user_id = caterer.id, cat_user.id
        cat_user_email = cat_user.email
        qr_id = _seed_request(
            s, status=QuoteRequestStatus.sent_to_caterers, target_caterer=caterer
        )
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/qualification/{qr_id}/edit",
            data={
                "meal_type": "cocktail_dejeunatoire",
                "event_date": (_dt.date.today() + _dt.timedelta(days=45)).strftime(
                    "%Y-%m-%d"
                ),
                "guest_count": "42",
                "event_city": "Lyon",
                "message_to_caterer": "Merci de revoir votre proposition.",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        s2 = session_factory()
        try:
            qr = s2.get(QuoteRequest, qr_id)
            assert qr.guest_count == 42
            assert qr.event_city == "Lyon"
            assert qr.meal_type == "cocktail_dejeunatoire"
            assert qr.message_to_caterer == "Merci de revoir votre proposition."
            # Notification in-app au user du traiteur
            notif = s2.scalar(
                select(Notification).where(
                    Notification.user_id == user_id,
                    Notification.type == "quote_request_updated",
                )
            )
            assert notif is not None
        finally:
            s2.close()
        # Email de modification envoyé au traiteur
        assert len(captured_emails) == 1
        assert captured_emails[0]["to"] == cat_user_email
        assert "modifi" in captured_emails[0]["subject"].lower()
    finally:
        _cleanup(qr_id=qr_id, caterer_id=caterer_id, user_id=user_id)


def test_edit_pending_request_does_not_notify(client, login, captured_emails):
    from database import session_factory
    from models import QuoteRequest, QuoteRequestStatus

    s = session_factory()
    try:
        qr_id = _seed_request(s, status=QuoteRequestStatus.pending_review)
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/qualification/{qr_id}/edit",
            data={"guest_count": "20", "event_city": "Nantes"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        s2 = session_factory()
        try:
            qr = s2.get(QuoteRequest, qr_id)
            assert qr.guest_count == 20
            assert qr.event_city == "Nantes"
        finally:
            s2.close()
        # Pas de demande envoyée → aucune re-notification
        assert captured_emails == []
    finally:
        _cleanup(qr_id=qr_id)


def test_edit_blocked_for_terminal_status(client, login):
    from database import session_factory
    from models import QuoteRequestStatus

    s = session_factory()
    try:
        qr_id = _seed_request(s, status=QuoteRequestStatus.completed)
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.get(f"/admin/qualification/{qr_id}/edit", follow_redirects=False)
        # Statut terminal : on ne peut plus éditer → redirection vers le détail
        assert r.status_code == 302
        assert f"/admin/qualification/{qr_id}" in r.headers["Location"]
    finally:
        _cleanup(qr_id=qr_id)


def test_edit_forbidden_for_non_admin(client, login):
    from database import session_factory
    from models import QuoteRequestStatus

    s = session_factory()
    try:
        qr_id = _seed_request(s, status=QuoteRequestStatus.sent_to_caterers)
        s.commit()
    finally:
        s.close()
    try:
        login("cook@test.local")  # role caterer
        r = client.get(f"/admin/qualification/{qr_id}/edit")
        assert r.status_code == 403
    finally:
        _cleanup(qr_id=qr_id)


def test_edit_get_renders_form_with_current_values(client, login):
    from database import session_factory
    from models import QuoteRequestStatus

    s = session_factory()
    try:
        qr_id = _seed_request(s, status=QuoteRequestStatus.sent_to_caterers)
        s.commit()
    finally:
        s.close()
    try:
        login("admin@test.local")
        r = client.get(f"/admin/qualification/{qr_id}/edit")
        assert r.status_code == 200
        assert b"Modifier la demande" in r.data
        # Ville pré-remplie
        assert b"Paris" in r.data
    finally:
        _cleanup(qr_id=qr_id)
