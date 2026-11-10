"""Une demande directe (client -> un traiteur choisi) passe par la
qualification super admin, comme une mise en concurrence :

* à la soumission, elle reste `pending_review`, le traiteur n'en voit
  rien (aucun QuoteRequestCaterer, aucune notification) et les super
  admins sont prévenus ;
* à l'approbation, elle part uniquement chez le traiteur choisi, sans
  passer par le matching ;
* si ce traiteur a été dévalidé entre-temps, rien n'est envoyé.
"""

from __future__ import annotations

import datetime as _dt
import re
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from models import (
    Caterer,
    CatererStructureType,
    Company,
    Notification,
    QRCStatus,
    QuoteRequest,
    QuoteRequestCaterer,
    QuoteRequestStatus,
    User,
)
from services import workflow

_CATERER_SIRET = "98765432109876"
_REDIRECT_QR_ID = re.compile(r"/client/requests/([0-9a-f-]{36})")


@pytest.fixture
def session(app):
    from database import session_factory

    s = session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _seed_direct_pending_qr(s, target: Caterer) -> uuid.UUID:
    acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
    alice = s.scalar(select(User).where(User.email == "alice@test.local"))
    qr = QuoteRequest(
        company_id=acme.id,
        user_id=alice.id,
        guest_count=10,
        status=QuoteRequestStatus.pending_review,
        is_compare_mode=False,
        target_caterer_id=target.id,
        event_city="Paris",
        event_date=_dt.date.today() + _dt.timedelta(days=30),
    )
    s.add(qr)
    s.flush()
    return qr.id


def _make_other_validated_caterer(s) -> Caterer:
    suffix = uuid.uuid4().hex[:6]
    c = Caterer(
        name=f"Other-{suffix}",
        siret=f"77{uuid.uuid4().int % 10**12:012d}"[:14],
        structure_type=CatererStructureType.ESAT,
        invoice_prefix=f"O{suffix[:5]}",
        is_validated=True,
        commission_rate=Decimal("0.05"),
    )
    s.add(c)
    s.flush()
    return c


def test_direct_submission_waits_for_review_and_stays_hidden_from_caterer(
    client, login
):
    from database import session_factory

    s = session_factory()
    try:
        caterer = s.scalar(select(Caterer).where(Caterer.siret == _CATERER_SIRET))
        caterer_id = caterer.id
        cook_id = s.scalar(select(User.id).where(User.email == "cook@test.local"))
        admin_id = s.scalar(select(User.id).where(User.email == "admin@test.local"))
    finally:
        s.close()

    login("alice@test.local")
    r = client.post(
        "/client/requests/new",
        data={"target_caterer_id": str(caterer_id), "is_compare_mode": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.data
    m = _REDIRECT_QR_ID.search(r.headers.get("Location", ""))
    assert m, r.headers.get("Location")
    qr_id = uuid.UUID(m.group(1))

    s = session_factory()
    try:
        qr = s.get(QuoteRequest, qr_id)
        assert qr.status == QuoteRequestStatus.pending_review
        assert qr.is_compare_mode is False
        assert qr.target_caterer_id == caterer_id
        assert not s.scalars(
            select(QuoteRequestCaterer).where(
                QuoteRequestCaterer.quote_request_id == qr_id
            )
        ).all()
        notified = set(
            s.scalars(
                select(Notification.user_id).where(
                    Notification.related_entity_id == qr_id
                )
            ).all()
        )
        assert cook_id not in notified
        assert admin_id in notified
    finally:
        s.execute(
            Notification.__table__.delete().where(
                Notification.related_entity_id == qr_id
            )
        )
        s.execute(QuoteRequest.__table__.delete().where(QuoteRequest.id == qr_id))
        s.commit()
        s.close()


def test_approve_direct_request_dispatches_only_to_target(session):
    target = session.scalar(select(Caterer).where(Caterer.siret == _CATERER_SIRET))
    _make_other_validated_caterer(session)
    qr_id = _seed_direct_pending_qr(session, target)

    qrcs = workflow.approve_quote_request(session, request_id=qr_id)
    session.flush()

    assert [q.caterer_id for q in qrcs] == [target.id]
    assert qrcs[0].status == QRCStatus.selected
    qr = session.get(QuoteRequest, qr_id)
    assert qr.status == QuoteRequestStatus.sent_to_caterers


def test_approve_direct_request_skips_matching(session):
    """Le client a choisi ce traiteur : une offre de service qui ne colle
    pas au type de repas ne doit pas bloquer l'envoi."""
    target = session.scalar(select(Caterer).where(Caterer.siret == _CATERER_SIRET))
    target.service_offerings = ["cocktail"]
    qr_id = _seed_direct_pending_qr(session, target)
    session.get(QuoteRequest, qr_id).meal_type = "petit_dejeuner"
    session.flush()

    qrcs = workflow.approve_quote_request(session, request_id=qr_id)

    assert [q.caterer_id for q in qrcs] == [target.id]


def test_approve_direct_request_with_unvalidated_target_sends_nothing(session):
    target = session.scalar(select(Caterer).where(Caterer.siret == _CATERER_SIRET))
    qr_id = _seed_direct_pending_qr(session, target)
    target.is_validated = False
    session.flush()

    qrcs = workflow.approve_quote_request(session, request_id=qr_id)
    session.flush()

    assert qrcs == []
    assert session.get(QuoteRequest, qr_id).status == QuoteRequestStatus.pending_review


def test_admin_sees_target_caterer_on_pending_direct_request(client, login):
    from database import session_factory

    s = session_factory()
    try:
        target = s.scalar(select(Caterer).where(Caterer.siret == _CATERER_SIRET))
        target_name = target.name
        qr_id = _seed_direct_pending_qr(s, target)
        s.commit()
    finally:
        s.close()

    try:
        login("admin@test.local")
        r = client.get("/admin/requests?status=pending_review")
        assert r.status_code == 200
        assert target_name in r.get_data(as_text=True)

        r = client.get(f"/admin/qualification/{qr_id}")
        body = r.get_data(as_text=True)
        assert r.status_code == 200
        assert target_name in body
        assert f"/admin/qualification/{qr_id}/approve" in body
    finally:
        s = session_factory()
        try:
            s.execute(QuoteRequest.__table__.delete().where(QuoteRequest.id == qr_id))
            s.commit()
        finally:
            s.close()


def test_client_sees_target_caterer_without_message_while_pending(client, login):
    from database import session_factory

    s = session_factory()
    try:
        target = s.scalar(select(Caterer).where(Caterer.siret == _CATERER_SIRET))
        target_name = target.name
        qr_id = _seed_direct_pending_qr(s, target)
        s.commit()
    finally:
        s.close()

    try:
        login("alice@test.local")
        r = client.get(f"/client/requests/{qr_id}")
        body = r.get_data(as_text=True)
        assert r.status_code == 200
        assert target_name in body
        assert "en cours de validation par notre équipe" in body
        assert "send-message-pending-" not in body
    finally:
        s = session_factory()
        try:
            s.execute(QuoteRequest.__table__.delete().where(QuoteRequest.id == qr_id))
            s.commit()
        finally:
            s.close()
