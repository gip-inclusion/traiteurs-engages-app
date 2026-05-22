import datetime as _dt
import uuid
from decimal import Decimal

import pytest

from models import (
    Caterer,
    CatererStructureType,
    Company,
    Order,
    OrderStatus,
    Quote,
    QuoteRequest,
    QuoteRequestStatus,
    QuoteStatus,
    User,
)
from services.impact import HOURS_FINANCED_DIVISOR_EUR, compute_social_impact


@pytest.fixture
def session(app):
    from database import session_factory

    s = session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _make_caterer(s, structure_type: CatererStructureType) -> Caterer:
    suffix = uuid.uuid4().hex[:6]
    c = Caterer(
        name=f"Impact Caterer {suffix}",
        siret=f"5{uuid.uuid4().hex[:13]}",
        structure_type=structure_type,
        invoice_prefix=f"IMP{suffix[:5].upper()}",
        is_validated=True,
    )
    s.add(c)
    s.flush()
    return c


def _seed_paid_order(
    s,
    *,
    caterer: Caterer,
    amount_ht: Decimal,
    requester_email: str = "alice@test.local",
    status: OrderStatus = OrderStatus.paid,
) -> Order:
    from sqlalchemy import select

    company = s.scalar(select(Company).where(Company.siret == "12345678901234"))
    requester = s.scalar(select(User).where(User.email == requester_email))

    qr = QuoteRequest(
        company_id=company.id,
        user_id=requester.id,
        guest_count=10,
        status=QuoteRequestStatus.completed,
        event_address="1 rue Test",
        event_city="Paris",
        event_zip_code="75001",
        event_date=_dt.date.today() + _dt.timedelta(days=30),
    )
    s.add(qr)
    s.flush()

    q = Quote(
        quote_request_id=qr.id,
        caterer_id=caterer.id,
        reference=f"DEVIS-IMP-{qr.id.hex[:8]}",
        total_amount_ht=amount_ht,
        status=QuoteStatus.accepted,
    )
    s.add(q)
    s.flush()

    o = Order(
        quote_id=q.id,
        client_admin_id=requester.id,
        status=status,
    )
    s.add(o)
    s.flush()
    return o


def test_compute_social_impact_empty_returns_zeros(session):
    from sqlalchemy import select

    company = session.scalar(select(Company).where(Company.siret == "12345678901234"))

    impact = compute_social_impact(session, company_id=company.id)

    assert impact.total_ht == Decimal("0")
    assert impact.siae_ht == Decimal("0")
    assert impact.stpa_ht == Decimal("0")
    assert impact.hours_financed == 0


def test_compute_social_impact_only_paid_orders_count(session):
    from sqlalchemy import select

    company = session.scalar(select(Company).where(Company.siret == "12345678901234"))
    caterer = _make_caterer(session, CatererStructureType.EI)

    _seed_paid_order(
        session, caterer=caterer, amount_ht=Decimal("400"), status=OrderStatus.paid
    )
    _seed_paid_order(
        session,
        caterer=caterer,
        amount_ht=Decimal("999"),
        status=OrderStatus.delivered,
    )
    _seed_paid_order(
        session,
        caterer=caterer,
        amount_ht=Decimal("888"),
        status=OrderStatus.invoiced,
    )

    impact = compute_social_impact(session, company_id=company.id)

    # Seul le 400€ payé compte ; les autres statuts sont exclus.
    assert impact.total_ht == Decimal("400")
    assert impact.siae_ht == Decimal("400")
    assert impact.stpa_ht == Decimal("0")


def test_compute_social_impact_splits_siae_and_stpa(session):
    from sqlalchemy import select

    company = session.scalar(select(Company).where(Company.siret == "12345678901234"))

    ei = _make_caterer(session, CatererStructureType.EI)
    aci = _make_caterer(session, CatererStructureType.ACI)
    esat = _make_caterer(session, CatererStructureType.ESAT)
    ea = _make_caterer(session, CatererStructureType.EA)

    _seed_paid_order(session, caterer=ei, amount_ht=Decimal("100"))
    _seed_paid_order(session, caterer=aci, amount_ht=Decimal("200"))
    _seed_paid_order(session, caterer=esat, amount_ht=Decimal("400"))
    _seed_paid_order(session, caterer=ea, amount_ht=Decimal("800"))

    impact = compute_social_impact(session, company_id=company.id)

    assert impact.total_ht == Decimal("1500")
    assert impact.siae_ht == Decimal("300"), "SIAE = EI + ACI = 100 + 200"
    assert impact.stpa_ht == Decimal("1200"), "STPA = ESAT + EA = 400 + 800"


def test_compute_social_impact_hours_use_lemarche_ratio(session):
    from sqlalchemy import select

    assert HOURS_FINANCED_DIVISOR_EUR == 26, (
        "ratio sourcé sur gip-inclusion/le-marche ; "
        "ne pas changer sans mettre à jour la docstring du service"
    )

    company = session.scalar(select(Company).where(Company.siret == "12345678901234"))
    caterer = _make_caterer(session, CatererStructureType.EI)

    # 1000€ → round(1000/26) = 38, exactement ce que la plateforme
    # affiche pour amount=1000 (cf. docstring du service).
    _seed_paid_order(session, caterer=caterer, amount_ht=Decimal("1000"))
    impact = compute_social_impact(session, company_id=company.id)
    assert impact.hours_financed == 38

    # 500€ → round(500/26) = 19 (idem cas de référence du calculateur).
    # On démarre une nouvelle agrégation côté DB : on retire l'order
    # précédente pour ne pas additionner.
    session.execute(Order.__table__.delete())
    session.execute(Quote.__table__.delete())
    session.execute(QuoteRequest.__table__.delete())
    session.flush()

    _seed_paid_order(session, caterer=caterer, amount_ht=Decimal("500"))
    impact = compute_social_impact(session, company_id=company.id)
    assert impact.hours_financed == 19


def test_compute_social_impact_scopes_to_requester_when_set(session):
    from sqlalchemy import select

    company = session.scalar(select(Company).where(Company.siret == "12345678901234"))
    alice = session.scalar(select(User).where(User.email == "alice@test.local"))
    bob = session.scalar(select(User).where(User.email == "bob@test.local"))
    caterer = _make_caterer(session, CatererStructureType.EI)

    _seed_paid_order(
        session,
        caterer=caterer,
        amount_ht=Decimal("100"),
        requester_email="alice@test.local",
    )
    _seed_paid_order(
        session,
        caterer=caterer,
        amount_ht=Decimal("300"),
        requester_email="bob@test.local",
    )

    # Sans scope (vue admin) : on voit les deux → 400.
    full = compute_social_impact(session, company_id=company.id)
    assert full.total_ht == Decimal("400")

    # Scopé à Bob : seules ses 300 remontent.
    bob_only = compute_social_impact(
        session, company_id=company.id, requester_user_id=bob.id
    )
    assert bob_only.total_ht == Decimal("300")

    # Scopé à Alice : seules ses 100 remontent.
    alice_only = compute_social_impact(
        session, company_id=company.id, requester_user_id=alice.id
    )
    assert alice_only.total_ht == Decimal("100")

    # Garde-fou : Bob et Alice sont bien deux users distincts de la
    # même entreprise — sinon le test ci-dessus serait trivialement vrai.
    assert alice.id != bob.id
    assert alice.company_id == bob.company_id == company.id


def test_client_dashboard_renders_impact_block(client, login):
    login("alice@test.local")
    resp = client.get("/client/dashboard")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")

    assert "Impact social" in body
    assert "Prix HT des achats inclusifs" in body
    assert "structures d'insertion (SIAE)" in body
    assert "secteur prot" in body and "STPA" in body
    assert "Nombre d'heures financ" in body
    # Mention obligatoire reproduite à l'identique depuis la consigne.
    assert (
        "Il s'agit d'une estimation bas" in body
        and "non repr" in body
        and "prestataires inclusifs" in body
    )
