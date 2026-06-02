from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid


def _sign(payload: str, secret: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    signed = f"{ts}.{payload}".encode()
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _event(
    event_type: str,
    data_object: dict,
    *,
    event_id: str | None = None,
) -> str:
    ev = {
        "id": event_id or f"evt_test_{uuid.uuid4().hex[:16]}",
        "object": "event",
        "type": event_type,
        "api_version": "2024-12-18.acacia",
        "created": int(time.time()),
        "data": {"object": data_object},
        "livemode": False,
        "pending_webhooks": 1,
        "request": {"id": None, "idempotency_key": None},
    }
    return json.dumps(ev, separators=(",", ":"))


TEST_SECRET = "whsec_test_" + "a" * 32


def test_empty_webhook_secret_rejects_forged_event(client, monkeypatch):
    import config

    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "")

    payload = _event("invoice.paid", {"id": "in_forged", "charge": "ch_forged"})
    header = _sign(payload, secret="")

    resp = client.post(
        "/api/webhooks/stripe",
        data=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": header},
    )
    assert resp.status_code >= 400, (
        f"empty-secret webhook should be rejected, got {resp.status_code}"
    )


def _seed_order_with_payment(stripe_invoice_id: str):
    import uuid as _uuid
    from decimal import Decimal
    from sqlalchemy import select

    from database import session_factory
    from models import (
        Caterer,
        Company,
        Order,
        OrderStatus,
        Payment,
        PaymentStatus,
        Quote,
        QuoteRequest,
        QuoteStatus,
        User,
    )

    s = session_factory()
    try:
        company = s.scalar(select(Company).where(Company.siret == "12345678901234"))
        caterer = s.scalar(select(Caterer).where(Caterer.siret == "98765432109876"))
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))

        qr = QuoteRequest(
            company_id=company.id,
            user_id=alice.id,
            guest_count=10,
        )
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
            stripe_invoice_id=stripe_invoice_id,
        )
        s.add(order)
        s.flush()

        payment = Payment(
            order_id=order.id,
            caterer_id=caterer.id,
            stripe_invoice_id=stripe_invoice_id,
            status=PaymentStatus.pending,
            amount_total_cents=12000,
            application_fee_cents=600,
            amount_to_caterer_cents=11400,
        )
        s.add(payment)
        s.commit()
        return order.id, payment.id
    finally:
        s.close()


def _load_payment(payment_id):
    from database import session_factory
    from models import Payment

    s = session_factory()
    try:
        return s.get(Payment, payment_id)
    finally:
        s.close()


def _load_order(order_id):
    from database import session_factory
    from models import Order

    s = session_factory()
    try:
        return s.get(Order, order_id)
    finally:
        s.close()


def test_signed_invoice_paid_marks_payment_succeeded(client, monkeypatch):
    import config

    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", TEST_SECRET)

    invoice_id = f"in_test_{uuid.uuid4().hex[:16]}"
    charge_id = f"ch_test_{uuid.uuid4().hex[:16]}"
    order_id, payment_id = _seed_order_with_payment(invoice_id)

    payload = _event(
        "invoice.paid",
        {"id": invoice_id, "object": "invoice", "charge": charge_id},
    )
    header = _sign(payload, TEST_SECRET)

    resp = client.post(
        "/api/webhooks/stripe",
        data=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": header},
    )
    assert resp.status_code == 200, (
        f"expected 200, got {resp.status_code}; body={resp.get_data(as_text=True)[:400]}"
    )

    from models import OrderStatus, PaymentStatus

    payment = _load_payment(payment_id)
    order = _load_order(order_id)
    assert payment.status == PaymentStatus.succeeded, payment.status
    assert payment.stripe_charge_id == charge_id
    assert order.status == OrderStatus.paid, order.status


def test_payment_failed_after_paid_does_not_downgrade(client, monkeypatch):
    import config

    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", TEST_SECRET)

    invoice_id = f"in_test_{uuid.uuid4().hex[:16]}"
    order_id, payment_id = _seed_order_with_payment(invoice_id)

    paid_payload = _event("invoice.paid", {"id": invoice_id, "charge": "ch_ok"})
    resp = client.post(
        "/api/webhooks/stripe",
        data=paid_payload,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": _sign(paid_payload, TEST_SECRET),
        },
    )
    assert resp.status_code == 200

    failed_payload = _event("invoice.payment_failed", {"id": invoice_id})
    resp = client.post(
        "/api/webhooks/stripe",
        data=failed_payload,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": _sign(failed_payload, TEST_SECRET),
        },
    )
    assert resp.status_code == 200

    from models import PaymentStatus

    payment = _load_payment(payment_id)
    assert payment.status == PaymentStatus.succeeded, (
        f"stale payment_failed wrongly downgraded to {payment.status}"
    )


def test_business_failure_rolls_back_dedup_row_so_retry_can_succeed(
    client, monkeypatch
):
    import config

    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", TEST_SECRET)

    invoice_id = f"in_test_{uuid.uuid4().hex[:16]}"
    event_id = f"evt_fault_{uuid.uuid4().hex[:16]}"
    order_id, payment_id = _seed_order_with_payment(invoice_id)

    payload = _event(
        "invoice.paid",
        {"id": invoice_id, "charge": "ch_fault_test"},
        event_id=event_id,
    )
    header = _sign(payload, TEST_SECRET)

    import services.reviews as reviews_module

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated downstream failure (C-1 fault injection)")

    monkeypatch.setattr(reviews_module, "notify_review_invite", _boom)

    resp = client.post(
        "/api/webhooks/stripe",
        data=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": header},
    )
    assert resp.status_code == 500, (
        f"fault must propagate as 500 (so Stripe retries); got {resp.status_code} "
        f"body={resp.get_data(as_text=True)[:300]}"
    )
    assert resp.is_json, "non-JSON 500 leaks an HTML page into Stripe's dashboard"
    assert resp.get_json().get("event_id") == event_id

    from models import PaymentStatus

    after_fault = _load_payment(payment_id)
    assert after_fault.status == PaymentStatus.pending, (
        f"payment must NOT be half-applied; got {after_fault.status}"
    )

    from sqlalchemy import select

    from database import session_factory
    from models import StripeEvent

    s = session_factory()
    try:
        ev = s.scalar(select(StripeEvent).where(StripeEvent.id == event_id))
        assert ev is None, (
            "dedup row survived a rolled-back transaction — retry will silently no-op"
        )
    finally:
        s.close()

    monkeypatch.undo()
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", TEST_SECRET)

    retry = client.post(
        "/api/webhooks/stripe",
        data=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": header},
    )
    assert retry.status_code == 200, (
        f"retry must succeed once the fault clears; got {retry.status_code}"
    )
    final = _load_payment(payment_id)
    assert final.status == PaymentStatus.succeeded


def test_duplicate_event_id_is_idempotent(client, monkeypatch):
    import config

    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", TEST_SECRET)

    invoice_id = f"in_test_{uuid.uuid4().hex[:16]}"
    _order_id, payment_id = _seed_order_with_payment(invoice_id)

    event_id = f"evt_dup_{uuid.uuid4().hex[:16]}"
    payload = _event(
        "invoice.paid",
        {"id": invoice_id, "charge": "ch_first"},
        event_id=event_id,
    )
    header = _sign(payload, TEST_SECRET)

    r1 = client.post(
        "/api/webhooks/stripe",
        data=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": header},
    )
    assert r1.status_code == 200

    from models import PaymentStatus

    first = _load_payment(payment_id)
    assert first.status == PaymentStatus.succeeded
    assert first.stripe_charge_id == "ch_first"

    from database import session_factory
    from models import Payment, PaymentStatus as PS

    s = session_factory()
    try:
        p = s.get(Payment, payment_id)
        p.status = PS.refunded
        p.stripe_charge_id = "ch_ADMIN_MANUAL"
        s.commit()
    finally:
        s.close()

    r2 = client.post(
        "/api/webhooks/stripe",
        data=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": header},
    )
    assert r2.status_code == 200

    replayed = _load_payment(payment_id)
    assert replayed.status == PS.refunded, (
        "duplicate event was processed again — not idempotent"
    )
    assert replayed.stripe_charge_id == "ch_ADMIN_MANUAL"
