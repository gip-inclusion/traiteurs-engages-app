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


def test_revision_reference_appends_version():
    assert revision_reference("DEVIS-X-2026-014", 2) == "DEVIS-X-2026-014-V2"


def test_revision_reference_replaces_existing_suffix():
    assert revision_reference("DEVIS-X-2026-014-V2", 3) == "DEVIS-X-2026-014-V3"


def test_start_revision_creates_prefilled_draft(session):
    qr_id, caterer, quote_id = _seed_sent_quote(session)
    rev = workflow.start_quote_revision(
        session, request_id=qr_id, quote_id=quote_id, caterer=caterer
    )
    assert rev.status == QuoteStatus.draft
    assert rev.version == 2
    assert rev.supersedes_id == quote_id
    assert rev.reference.endswith("-V2")

    assert rev.notes == "Devis initial"
    assert rev.total_amount_ht == Decimal("250")
    assert len(rev.lines) == 1
    assert rev.lines[0].description == "Plateau repas"


def test_start_revision_is_idempotent(session):
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
    qr_id, caterer, quote_id = _seed_sent_quote(session)
    q = session.get(Quote, quote_id)
    q.status = QuoteStatus.draft
    session.flush()
    with pytest.raises(workflow.QuoteNotFound):
        workflow.start_quote_revision(
            session, request_id=qr_id, quote_id=quote_id, caterer=caterer
        )


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


def test_superseded_quote_not_revisable_but_active_version_is(session):
    qr_id, caterer, v1_id = _seed_sent_quote(session)
    v2 = workflow.start_quote_revision(
        session, request_id=qr_id, quote_id=v1_id, caterer=caterer
    )
    session.flush()
    workflow.submit_quote(session, request_id=qr_id, quote_id=v2.id, caterer=caterer)
    session.flush()

    with pytest.raises(workflow.QuoteNotFound):
        workflow.start_quote_revision(
            session, request_id=qr_id, quote_id=v1_id, caterer=caterer
        )

    v3 = workflow.start_quote_revision(
        session, request_id=qr_id, quote_id=v2.id, caterer=caterer
    )
    assert v3.version == 3
    assert v3.reference.endswith("-V3")
    assert v3.supersedes_id == v2.id


def test_submitting_revision_refuses_when_source_was_refused(session):
    qr_id, caterer, v1_id = _seed_sent_quote(session)
    v2 = workflow.start_quote_revision(
        session, request_id=qr_id, quote_id=v1_id, caterer=caterer
    )
    session.flush()
    v1 = session.get(Quote, v1_id)
    v1.status = QuoteStatus.refused
    session.flush()

    with pytest.raises(workflow.QuoteNotAvailable):
        workflow.submit_quote(
            session, request_id=qr_id, quote_id=v2.id, caterer=caterer
        )
    session.refresh(v1)
    session.refresh(v2)
    assert v1.status == QuoteStatus.refused
    assert v2.status == QuoteStatus.draft


def test_revision_allowed_even_when_three_already_transmitted(session):
    qr_id, caterer, quote_id = _seed_sent_quote(session, prior_transmitted=3)
    rev = workflow.start_quote_revision(
        session, request_id=qr_id, quote_id=quote_id, caterer=caterer
    )
    session.flush()

    workflow.submit_quote(session, request_id=qr_id, quote_id=rev.id, caterer=caterer)
    qrc = session.scalar(
        select(QuoteRequestCaterer).where(
            QuoteRequestCaterer.quote_request_id == qr_id,
            QuoteRequestCaterer.caterer_id == caterer.id,
        )
    )
    assert qrc.status == QRCStatus.transmitted_to_client
