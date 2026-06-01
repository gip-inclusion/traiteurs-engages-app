import pytest


def _seed_order():
    import uuid as _uuid
    from decimal import Decimal
    from sqlalchemy import select

    from database import session_factory
    from models import (
        Caterer,
        Company,
        Order,
        OrderStatus,
        Quote,
        QuoteRequest,
        QuoteStatus,
        User,
    )

    s = session_factory()
    try:
        acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
        caterer = s.scalar(select(Caterer).where(Caterer.siret == "98765432109876"))
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))
        qr = QuoteRequest(company_id=acme.id, user_id=alice.id, guest_count=10)
        s.add(qr)
        s.flush()
        quote = Quote(
            quote_request_id=qr.id,
            caterer_id=caterer.id,
            reference=f"DEVIS-TST-{_uuid.uuid4().hex[:6].upper()}",
            total_amount_ht=Decimal("100"),
            status=QuoteStatus.accepted,
        )
        s.add(quote)
        s.flush()
        order = Order(
            quote_id=quote.id,
            client_admin_id=alice.id,
            status=OrderStatus.invoiced,
        )
        s.add(order)
        s.commit()
        return order.id, caterer.id
    finally:
        s.close()


def test_duplicate_stripe_invoice_id_is_rejected(app):
    from sqlalchemy.exc import IntegrityError

    from database import session_factory
    from models import Payment, PaymentStatus

    order_id, caterer_id = _seed_order()
    invoice_id = "in_unique_test_1"

    s1 = session_factory()
    try:
        s1.add(
            Payment(
                order_id=order_id,
                caterer_id=caterer_id,
                stripe_invoice_id=invoice_id,
                status=PaymentStatus.pending,
            )
        )
        s1.commit()
    finally:
        s1.close()

    s2 = session_factory()
    try:
        s2.add(
            Payment(
                order_id=order_id,
                caterer_id=caterer_id,
                stripe_invoice_id=invoice_id,
                status=PaymentStatus.pending,
            )
        )
        with pytest.raises(IntegrityError):
            s2.commit()
    finally:
        s2.close()
