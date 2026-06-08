"""Tests autour du listing/refus des demandes côté traiteur."""

import datetime as _dt
import uuid
from types import SimpleNamespace

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


def _qrc(status, caterer_id):
    return SimpleNamespace(status=status, caterer_id=caterer_id)


def _qr(*, caterers, quotes):
    return SimpleNamespace(caterers=caterers, quotes=quotes)


# ---------------------------------------------------------------------------
# Unitaires : _derive_qrc_display_status
# ---------------------------------------------------------------------------


def test_display_status_selected_no_quote_is_new():
    from blueprints.caterer.requests import _derive_qrc_display_status
    from models import QRCStatus

    caterer_id = uuid.uuid4()
    qr = _qr(caterers=[_qrc(QRCStatus.selected, caterer_id)], quotes=[])
    assert _derive_qrc_display_status(qr, caterer_id) == "new"


def test_display_status_rejected_is_closed_even_without_quote():
    """Bug du refus sans devis : le QRC.rejected doit basculer la
    demande dans l'onglet 'Cloturees', pas rester en 'Nouvelles'.
    """
    from blueprints.caterer.requests import _derive_qrc_display_status
    from models import QRCStatus

    caterer_id = uuid.uuid4()
    qr = _qr(caterers=[_qrc(QRCStatus.rejected, caterer_id)], quotes=[])
    assert _derive_qrc_display_status(qr, caterer_id) == "closed"


def test_display_status_rejected_with_draft_quote_is_closed():
    """request_reject empeche le refus apres envoi d'un devis 'sent',
    mais un draft existant ne doit pas faire basculer le statut.
    """
    from blueprints.caterer.requests import _derive_qrc_display_status
    from models import QRCStatus, QuoteStatus

    caterer_id = uuid.uuid4()
    draft = SimpleNamespace(
        caterer_id=caterer_id,
        status=QuoteStatus.draft,
        supersedes_id=None,
        version=1,
    )
    qr = _qr(caterers=[_qrc(QRCStatus.rejected, caterer_id)], quotes=[draft])
    assert _derive_qrc_display_status(qr, caterer_id) == "closed"


def test_display_status_closed_remains_closed():
    from blueprints.caterer.requests import _derive_qrc_display_status
    from models import QRCStatus

    caterer_id = uuid.uuid4()
    qr = _qr(caterers=[_qrc(QRCStatus.closed, caterer_id)], quotes=[])
    assert _derive_qrc_display_status(qr, caterer_id) == "closed"


# ---------------------------------------------------------------------------
# Integration : POST /caterer/requests/<id>/reject
# ---------------------------------------------------------------------------


def _seed_selected_qrc(s, caterer_id):
    from sqlalchemy import select

    from models import (
        Company,
        QRCStatus,
        QuoteRequest,
        QuoteRequestCaterer,
        QuoteRequestStatus,
        User,
    )

    acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
    alice = s.scalar(select(User).where(User.email == "alice@test.local"))
    qr = QuoteRequest(
        company_id=acme.id,
        user_id=alice.id,
        guest_count=14,
        status=QuoteRequestStatus.sent_to_caterers,
        event_address="1 rue Test",
        event_city="Paris",
        event_zip_code="75001",
        event_date=_dt.date.today() + _dt.timedelta(days=21),
    )
    s.add(qr)
    s.flush()
    qrc = QuoteRequestCaterer(
        quote_request_id=qr.id,
        caterer_id=caterer_id,
        status=QRCStatus.selected,
    )
    s.add(qrc)
    s.commit()
    return qr.id


def test_post_reject_flips_qrc_status_and_moves_to_closed_tab(
    app, client, login, session
):
    from sqlalchemy import select

    from models import Caterer, QRCStatus, QuoteRequestCaterer

    fixture_caterer = session.scalar(
        select(Caterer).where(Caterer.siret == "98765432109876")
    )
    qr_id = _seed_selected_qrc(session, fixture_caterer.id)

    login("cook@test.local")
    resp = client.post(
        f"/caterer/requests/{qr_id}/reject",
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # DB : le QRC est bien rejected.
    qrc = session.scalar(
        select(QuoteRequestCaterer).where(
            QuoteRequestCaterer.quote_request_id == qr_id,
            QuoteRequestCaterer.caterer_id == fixture_caterer.id,
        )
    )
    session.refresh(qrc)
    assert qrc.status == QRCStatus.rejected

    # UI : l'onglet "Nouvelles" ne doit plus afficher la demande,
    # l'onglet "Cloturees" doit la contenir.
    new_tab = client.get("/caterer/requests?status=new")
    closed_tab = client.get("/caterer/requests?status=closed")
    assert new_tab.status_code == 200
    assert closed_tab.status_code == 200
    qr_str = str(qr_id)
    assert qr_str not in new_tab.get_data(as_text=True)
    assert qr_str in closed_tab.get_data(as_text=True)

    # Cleanup : on rollback la commit faite par _seed_selected_qrc et le
    # POST, pour ne pas polluer les tests suivants.
    session.execute(
        QuoteRequestCaterer.__table__.delete().where(
            QuoteRequestCaterer.quote_request_id == qr_id
        )
    )
    from models import QuoteRequest

    session.execute(QuoteRequest.__table__.delete().where(QuoteRequest.id == qr_id))
    session.commit()
