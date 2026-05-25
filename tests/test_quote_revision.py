"""Tests de la révision de devis (« DEVIS-…-V2 »).

Flux : un traiteur a un devis ENVOYÉ (`sent`) ; il le révise. Une copie
brouillon est créée (`start_quote_revision`), éditée puis envoyée
(`submit_quote`, qui détecte `supersedes_id`). L'ancien devis bascule
`superseded`, la révision devient `sent`, et le traiteur conserve son
slot (la règle des 3 répondants n'est pas réappliquée).
"""

import datetime as _dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from models import (
    Caterer,
    CatererStructureType,
    Company,
    QRCStatus,
    Quote,
    QuoteLine,
    QuoteRequest,
    QuoteRequestCaterer,
    QuoteRequestStatus,
    QuoteStatus,
    User,
)
from services import workflow
from services.quotes import revision_reference


@pytest.fixture
def session(app):
    from database import session_factory

    s = session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _seed_sent_quote(s, *, prior_transmitted: int = 1):
    """QR ouverte + un traiteur « à nous » dont le QRC est
    `transmitted_to_client` et le devis `sent` (avec 1 ligne).

    `prior_transmitted` = nombre TOTAL de traiteurs ayant transmis, le
    nôtre compris. À 3, on est en pleine règle des 3 répondants —
    pratique pour vérifier que la révision n'est pas bloquée.
    Retourne (qr_id, notre_caterer, notre_quote_id)."""
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
        event_date=_dt.date.today() + _dt.timedelta(days=30),
    )
    s.add(qr)
    s.flush()

    # Traiteurs "remplisseurs" déjà transmis (pour saturer la règle des 3).
    for i in range(max(0, prior_transmitted - 1)):
        c = Caterer(
            name=f"Filler {i} {uuid.uuid4().hex[:6]}",
            siret=f"77{uuid.uuid4().hex[:12]}",
            structure_type=CatererStructureType.ESAT,
            invoice_prefix=f"F{i}{uuid.uuid4().hex[:4]}",
            is_validated=True,
        )
        s.add(c)
        s.flush()
        s.add(
            QuoteRequestCaterer(
                quote_request_id=qr.id,
                caterer_id=c.id,
                status=QRCStatus.transmitted_to_client,
                response_rank=i + 1,
            )
        )
        s.add(
            Quote(
                quote_request_id=qr.id,
                caterer_id=c.id,
                reference=f"DEVIS-F{i}-{uuid.uuid4().hex[:8]}",
                total_amount_ht=Decimal("100"),
                status=QuoteStatus.sent,
            )
        )
    s.flush()

    caterer = Caterer(
        name=f"Mine {uuid.uuid4().hex[:6]}",
        siret=f"88{uuid.uuid4().hex[:12]}",
        structure_type=CatererStructureType.ESAT,
        invoice_prefix=f"M{uuid.uuid4().hex[:5]}",
        is_validated=True,
    )
    s.add(caterer)
    s.flush()
    s.add(
        QuoteRequestCaterer(
            quote_request_id=qr.id,
            caterer_id=caterer.id,
            status=QRCStatus.transmitted_to_client,
            response_rank=prior_transmitted,
        )
    )
    quote = Quote(
        quote_request_id=qr.id,
        caterer_id=caterer.id,
        reference=f"DEVIS-{caterer.invoice_prefix}-2026-001",
        total_amount_ht=Decimal("250"),
        amount_per_person=Decimal("25"),
        notes="Devis initial",
        valid_until=_dt.date.today() + _dt.timedelta(days=15),
        status=QuoteStatus.sent,
        version=1,
    )
    s.add(quote)
    s.flush()
    s.add(
        QuoteLine(
            quote_id=quote.id,
            position=0,
            section="principal",
            description="Plateau repas",
            quantity=Decimal("10"),
            unit_price_ht=Decimal("25"),
            tva_rate=Decimal("10"),
        )
    )
    s.flush()
    return qr.id, caterer, quote.id


# ---------------------------------------------------------------------------
# Helper de référence
# ---------------------------------------------------------------------------


def test_revision_reference_appends_version():
    assert revision_reference("DEVIS-X-2026-014", 2) == "DEVIS-X-2026-014-V2"


def test_revision_reference_replaces_existing_suffix():
    assert revision_reference("DEVIS-X-2026-014-V2", 3) == "DEVIS-X-2026-014-V3"


# ---------------------------------------------------------------------------
# start_quote_revision
# ---------------------------------------------------------------------------


def test_start_revision_creates_prefilled_draft(session):
    qr_id, caterer, quote_id = _seed_sent_quote(session)
    rev = workflow.start_quote_revision(
        session, request_id=qr_id, quote_id=quote_id, caterer=caterer
    )
    assert rev.status == QuoteStatus.draft
    assert rev.version == 2
    assert rev.supersedes_id == quote_id
    assert rev.reference.endswith("-V2")
    # Champs copiés depuis l'original
    assert rev.notes == "Devis initial"
    assert rev.total_amount_ht == Decimal("250")
    assert len(rev.lines) == 1
    assert rev.lines[0].description == "Plateau repas"


def test_start_revision_is_idempotent(session):
    """Deux clics « Modifier mon devis » ne créent qu'un brouillon."""
    qr_id, caterer, quote_id = _seed_sent_quote(session)
    rev1 = workflow.start_quote_revision(
        session, request_id=qr_id, quote_id=quote_id, caterer=caterer
    )
    session.flush()
    rev2 = workflow.start_quote_revision(
        session, request_id=qr_id, quote_id=quote_id, caterer=caterer
    )
    assert rev1.id == rev2.id


def test_start_revision_refuses_non_sent_quote(session):
    """On ne révise qu'un devis ENVOYÉ — un brouillon n'est pas révisable."""
    qr_id, caterer, quote_id = _seed_sent_quote(session)
    q = session.get(Quote, quote_id)
    q.status = QuoteStatus.draft
    session.flush()
    with pytest.raises(workflow.QuoteNotFound):
        workflow.start_quote_revision(
            session, request_id=qr_id, quote_id=quote_id, caterer=caterer
        )


# ---------------------------------------------------------------------------
# submit_quote sur une révision
# ---------------------------------------------------------------------------


def test_submitting_revision_supersedes_old_quote(session):
    qr_id, caterer, quote_id = _seed_sent_quote(session)
    rev = workflow.start_quote_revision(
        session, request_id=qr_id, quote_id=quote_id, caterer=caterer
    )
    session.flush()
    result = workflow.submit_quote(
        session, request_id=qr_id, quote_id=rev.id, caterer=caterer
    )
    assert result.status == QuoteStatus.sent
    old = session.get(Quote, quote_id)
    assert old.status == QuoteStatus.superseded


def test_revision_allowed_even_when_three_already_transmitted(session):
    """Le traiteur qui révise fait partie des 3 répondants : la règle
    des 3 ne doit PAS le bloquer (pas de QuoteRequestClosed) et son QRC
    reste `transmitted_to_client`."""
    qr_id, caterer, quote_id = _seed_sent_quote(session, prior_transmitted=3)
    rev = workflow.start_quote_revision(
        session, request_id=qr_id, quote_id=quote_id, caterer=caterer
    )
    session.flush()
    # Ne doit pas lever QuoteRequestClosed
    workflow.submit_quote(session, request_id=qr_id, quote_id=rev.id, caterer=caterer)
    qrc = session.scalar(
        select(QuoteRequestCaterer).where(
            QuoteRequestCaterer.quote_request_id == qr_id,
            QuoteRequestCaterer.caterer_id == caterer.id,
        )
    )
    assert qrc.status == QRCStatus.transmitted_to_client
