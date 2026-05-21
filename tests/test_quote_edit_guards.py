
import datetime as _dt
import json
from decimal import Decimal


def _seed_request_with_quote(status_literal: str):
    from sqlalchemy import select

    from database import session_factory
    from models import (
        Caterer,
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

    status = QuoteStatus(status_literal)

    s = session_factory()
    try:
        acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
        caterer = s.scalar(select(Caterer).where(Caterer.siret == "98765432109876"))
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

        qrc = QuoteRequestCaterer(
            quote_request_id=qr.id,
            caterer_id=caterer.id,
            status=QRCStatus.selected,
        )
        s.add(qrc)
        s.flush()

        quote = Quote(
            quote_request_id=qr.id,
            caterer_id=caterer.id,
            reference=f"DEVIS-EDT-{qr.id.hex[:6].upper()}",
            total_amount_ht=Decimal("1000"),
            amount_per_person=Decimal("100"),
            status=status,
            valid_until=_dt.date.today() + _dt.timedelta(days=14),
        )
        s.add(quote)
        s.flush()
        s.add(
            QuoteLine(
                quote_id=quote.id,
                description="Plateau initial",
                unit_price_ht=Decimal("100"),
                quantity=Decimal("10"),
            )
        )
        s.commit()
        return qr.id, quote.id
    finally:
        s.close()


def _quote_total(quote_id) -> Decimal:
    from database import session_factory
    from models import Quote

    s = session_factory()
    try:
        return s.get(Quote, quote_id).total_amount_ht
    finally:
        s.close()


def _malicious_payload():
    return {
        "details": json.dumps(
            [
                {
                    "description": "Plateau revu a la baisse",
                    "unit_price_ht": "0.10",
                    "quantity": 10,
                }
            ]
        ),
        "notes": "edit attempt",
        "valid_until": "",
    }


def test_editing_sent_quote_is_rejected(client, login):
    qr_id, quote_id = _seed_request_with_quote("sent")
    login("cook@test.local")
    resp = client.post(
        f"/caterer/requests/{qr_id}/quote/{quote_id}/edit",
        data=_malicious_payload(),
    )
    assert resp.status_code in (302, 400, 403, 409), resp.status_code
    assert _quote_total(quote_id) == Decimal("1000"), (
        "sent quote must not be editable — total should be untouched"
    )


def test_editing_accepted_quote_is_rejected(client, login):
    qr_id, quote_id = _seed_request_with_quote("accepted")
    login("cook@test.local")
    resp = client.post(
        f"/caterer/requests/{qr_id}/quote/{quote_id}/edit",
        data=_malicious_payload(),
    )
    assert resp.status_code in (302, 400, 403, 409), resp.status_code
    assert _quote_total(quote_id) == Decimal("1000"), (
        "accepted quote must never be editable — fraud vector"
    )


def test_editing_refused_quote_is_rejected(client, login):
    qr_id, quote_id = _seed_request_with_quote("refused")
    login("cook@test.local")
    resp = client.post(
        f"/caterer/requests/{qr_id}/quote/{quote_id}/edit",
        data=_malicious_payload(),
    )
    assert resp.status_code in (302, 400, 403, 409), resp.status_code
    assert _quote_total(quote_id) == Decimal("1000")


def test_editing_draft_quote_still_works(client, login):
    qr_id, quote_id = _seed_request_with_quote("draft")
    login("cook@test.local")
    resp = client.post(
        f"/caterer/requests/{qr_id}/quote/{quote_id}/edit",
        data={
            "details": json.dumps(
                [
                    {
                        "description": "Plateau revu",
                        "unit_price_ht": "120",
                        "quantity": 10,
                    }
                ]
            ),
            "notes": "tweak",
            "valid_until": "",
        },
    )
    assert resp.status_code in (200, 302), resp.status_code
    assert _quote_total(quote_id) == Decimal("1200"), (
        "draft quote should still be editable"
    )
