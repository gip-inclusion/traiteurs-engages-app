import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import stripe

import config
from models import (
    Caterer,
    CommissionInvoice,
    Invoice,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
)
from services.quotes import calculate_quote_totals, derive_invoice_reference

_billing_logger = logging.getLogger("app.billing")

CENTS_PER_EURO = Decimal("100")


def _to_cents(amount: Decimal) -> int:
    return int((amount * CENTS_PER_EURO).quantize(Decimal("1")))


@dataclass(frozen=True)
class InvoiceAmounts:
    invoice_total_cents: int
    application_fee_cents: int
    amount_to_caterer_cents: int


def split_invoice_amounts(
    *,
    total_ttc: Decimal,
    fee_ht: Decimal,
    fee_tva: Decimal,
) -> InvoiceAmounts:
    fee_ttc_cents = _to_cents(fee_ht) + _to_cents(fee_tva)
    total_ttc_cents = _to_cents(total_ttc)
    invoice_total_cents = total_ttc_cents + fee_ttc_cents
    return InvoiceAmounts(
        invoice_total_cents=invoice_total_cents,
        application_fee_cents=fee_ttc_cents,
        amount_to_caterer_cents=invoice_total_cents - fee_ttc_cents,
    )


STRIPE_API_VERSION = "2024-12-18.acacia"

if config.STRIPE_SECRET_KEY:
    stripe.api_key = config.STRIPE_SECRET_KEY
    stripe.api_version = STRIPE_API_VERSION


def create_connect_account(caterer: Caterer) -> dict[str, Any]:
    user = caterer.users[0] if caterer.users else None
    email = user.email if user else None
    account = stripe.Account.create(
        type="express",
        country="FR",
        email=email,
        business_type="company",
        capabilities={
            "card_payments": {"requested": True},
            "transfers": {"requested": True},
        },
        metadata={"caterer_id": str(caterer.id)},
        idempotency_key=f"caterer-account-{caterer.id}",
    )
    return account


def create_account_link(account_id: str, refresh_url: str, return_url: str) -> str:
    link = stripe.AccountLink.create(
        account=account_id,
        refresh_url=refresh_url,
        return_url=return_url,
        type="account_onboarding",
    )
    return link.url


def get_account(account_id: str) -> dict[str, bool]:
    account = stripe.Account.retrieve(account_id)
    return {
        "charges_enabled": bool(getattr(account, "charges_enabled", False)),
        "payouts_enabled": bool(getattr(account, "payouts_enabled", False)),
    }


def get_or_create_customer(session, user) -> str:
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = stripe.Customer.create(
        email=user.email,
        name=f"{user.first_name} {user.last_name}",
        metadata={"user_id": str(user.id)},
        idempotency_key=f"customer-user-{user.id}",
    )
    user.stripe_customer_id = customer.id
    session.add(user)
    return customer.id


def _create_or_get_tax_rate(percentage: Decimal, description: str) -> str:
    pct_str = str(percentage)
    existing = stripe.TaxRate.list(active=True, limit=100)
    for rate in existing.auto_paging_iter():
        if str(rate.percentage) == pct_str and rate.country == "FR":
            return rate.id
    new_rate = stripe.TaxRate.create(
        display_name="TVA",
        description=description,
        percentage=pct_str,
        inclusive=False,
        country="FR",
        jurisdiction="FR",
    )
    return new_rate.id


def create_invoice_for_order(session, order: Order) -> dict[str, Any]:
    quote = order.quote
    caterer = quote.caterer
    client_user = order.client_admin

    line_dicts = [
        {
            "section": ln.section,
            "description": ln.description or "",
            "quantity": float(ln.quantity),
            "unit_price_ht": float(ln.unit_price_ht),
            "tva_rate": float(ln.tva_rate),
        }
        for ln in quote.lines
    ]
    totals = calculate_quote_totals(
        line_dicts,
        quote.quote_request.guest_count,
        commission_rate=caterer.commission_rate,
    )

    customer_id = get_or_create_customer(session, client_user)

    platform_fee_ht = totals["platform_fee_ht"]
    platform_fee_tva = totals["platform_fee_tva"]
    platform_fee_ttc_cents = _to_cents(platform_fee_ht + platform_fee_tva)

    invoice_ref = derive_invoice_reference(quote.reference)

    order.invoice_attempt += 1
    session.flush()

    invoice = stripe.Invoice.create(
        customer=customer_id,
        collection_method="send_invoice",
        days_until_due=30,
        transfer_data={"destination": caterer.stripe_account_id},
        application_fee_amount=platform_fee_ttc_cents,
        metadata={
            "order_id": str(order.id),
            "invoice_reference": invoice_ref,
        },
        custom_fields=[
            {"name": "Traiteur", "value": caterer.name},
            {"name": "SIRET", "value": caterer.siret or ""},
        ],
        idempotency_key=f"invoice-order-{order.id}-v{order.invoice_attempt}",
    )

    tva_grouped: dict[str, dict] = {}
    for ln in quote.lines:
        tva_rate = str(ln.tva_rate)
        if tva_rate not in tva_grouped:
            tva_grouped[tva_rate] = {"amount_ht": Decimal("0"), "descriptions": []}
        line_ht = ln.quantity * ln.unit_price_ht
        tva_grouped[tva_rate]["amount_ht"] += line_ht
        tva_grouped[tva_rate]["descriptions"].append(ln.description or "")

    for tva_rate_str, group in tva_grouped.items():
        tva_pct = Decimal(tva_rate_str)
        tax_rate_id = _create_or_get_tax_rate(tva_pct, f"TVA {tva_rate_str}%")
        amount_cents = _to_cents(group["amount_ht"])
        desc = ", ".join(d for d in group["descriptions"] if d)
        stripe.InvoiceItem.create(
            customer=customer_id,
            invoice=invoice.id,
            amount=amount_cents,
            currency="eur",
            description=desc or "Prestation traiteur",
            tax_rates=[tax_rate_id],
        )

    fee_tax_rate_id = _create_or_get_tax_rate(Decimal("20"), "TVA 20%")
    fee_ht_cents = _to_cents(platform_fee_ht)
    stripe.InvoiceItem.create(
        customer=customer_id,
        invoice=invoice.id,
        amount=fee_ht_cents,
        currency="eur",
        description="Frais de mise en relation",
        tax_rates=[fee_tax_rate_id],
    )

    stripe.Invoice.finalize_invoice(invoice.id)
    sent = stripe.Invoice.send_invoice(invoice.id)
    hosted_url = sent.get("hosted_invoice_url", "") or ""

    order.stripe_invoice_id = invoice.id
    order.stripe_hosted_invoice_url = hosted_url
    order.status = OrderStatus.invoiced

    amounts = split_invoice_amounts(
        total_ttc=totals["total_ttc"],
        fee_ht=platform_fee_ht,
        fee_tva=platform_fee_tva,
    )

    payment = Payment(
        order_id=order.id,
        caterer_id=caterer.id,
        stripe_invoice_id=invoice.id,
        status=PaymentStatus.pending,
        amount_total_cents=amounts.invoice_total_cents,
        application_fee_cents=amounts.application_fee_cents,
        amount_to_caterer_cents=amounts.amount_to_caterer_cents,
    )
    session.add(payment)

    total_ht = totals["total_ht"]
    total_tva = totals["total_tva"]
    avg_tva_rate = (
        (total_tva / total_ht).quantize(Decimal("0.0001")) if total_ht else None
    )

    invoice_record = Invoice(
        order_id=order.id,
        caterer_id=caterer.id,
        reference=invoice_ref,
        amount_ht=total_ht,
        tva_rate=avg_tva_rate,
        amount_ttc=totals["total_ttc"],
        esat_mention=f"Structure {caterer.structure_type}"
        if caterer.structure_type
        else None,
    )
    session.add(invoice_record)

    commission_client = CommissionInvoice(
        order_id=order.id,
        party="client",
        amount_ht=platform_fee_ht,
        tva_rate=Decimal("0.20"),
        amount_ttc=platform_fee_ht + platform_fee_tva,
    )
    session.add(commission_client)

    commission_caterer = CommissionInvoice(
        order_id=order.id,
        party="caterer",
        amount_ht=platform_fee_ht,
        tva_rate=Decimal("0.20"),
        amount_ttc=platform_fee_ht + platform_fee_tva,
    )
    session.add(commission_caterer)

    _billing_logger.info(
        "invoice_created",
        extra={
            "event": "invoice_created",
            "order_id": str(order.id),
            "caterer_id": str(caterer.id),
            "invoice_reference": invoice_ref,
            "stripe_invoice_id": invoice.get("id"),
            "amount_ttc": float(totals["total_ttc"]),
            "platform_fee_ttc_cents": platform_fee_ttc_cents,
            "attempt": order.invoice_attempt,
        },
    )

    return invoice


def verify_webhook_signature(payload: bytes | str, sig_header: str, secret: str):
    try:
        return stripe.Webhook.construct_event(payload, sig_header, secret)
    except stripe.error.SignatureVerificationError as exc:
        raise ValueError(str(exc)) from exc
    except ValueError:
        raise
