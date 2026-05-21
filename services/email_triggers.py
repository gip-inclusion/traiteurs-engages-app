# Best-effort: every trigger wraps its enqueue in try/except so a Brevo
# or queue hiccup can't roll back the business write that just committed.
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from models import (
    Caterer,
    Order,
    QRCStatus,
    Quote,
    QuoteRequest,
    QuoteRequestCaterer,
    User,
)
from services.email import render_and_send_async


logger = logging.getLogger(__name__)


def _safe(label: str):
    # Bare except is deliberate (BLE001): any email-side bug must not
    # 500 the request that already committed the business write.
    def deco(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:  # noqa: BLE001
                logger.warning("email trigger %s failed", label, exc_info=True)
                return None

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return deco


@_safe("welcome_signup")
def welcome_signup(user: User, *, role_kind: str, cta_path: str) -> None:
    # role_kind ∈ {"client", "caterer", "admin"} drives the body.
    render_and_send_async(
        to=user.email,
        subject="Bienvenue chez Les Traiteurs Engagés",
        template_name="welcome",
        user=user,
        role_kind=role_kind,
        cta_url=f"{config.BASE_URL}{cta_path}",
    )


@_safe("quote_received")
def quote_received(db: Session, *, quote: Quote, caterer: Caterer) -> None:
    # No-op unless the QRC is `transmitted_to_client` — defence in depth
    # so a future caller (CLI, admin resend) can't email on the wrong state.
    qrc = db.scalar(
        select(QuoteRequestCaterer).where(
            QuoteRequestCaterer.quote_request_id == quote.quote_request_id,
            QuoteRequestCaterer.caterer_id == caterer.id,
        )
    )
    if qrc is None or qrc.status != QRCStatus.transmitted_to_client:
        return
    qr = db.get(QuoteRequest, quote.quote_request_id)
    if qr is None or qr.user_id is None:
        return
    user = db.get(User, qr.user_id)
    if user is None or not user.is_active:
        return

    cta_url = f"{config.BASE_URL}/client/requests/{qr.id}"
    render_and_send_async(
        to=user.email,
        subject="Vous avez reçu un devis",
        template_name="quote_received",
        user=user,
        caterer=caterer,
        event_date=qr.event_date,
        total_amount_ht=quote.total_amount_ht,
        amount_per_person=quote.amount_per_person,
        valid_until=quote.valid_until,
        cta_url=cta_url,
    )


@_safe("order_confirmed")
def order_confirmed(db: Session, *, order: Order) -> None:
    # Per-user enqueue (not a bulk send): the To: array would leak
    # recipients to each other and lose `{{ user.first_name }}`.
    quote = db.get(Quote, order.quote_id)
    if quote is None:
        return
    caterer = db.get(Caterer, quote.caterer_id) if quote.caterer_id else None
    qr = (
        db.get(QuoteRequest, quote.quote_request_id) if quote.quote_request_id else None
    )
    if caterer is None or qr is None:
        return

    company = qr.company
    recipients = [u for u in (caterer.users or []) if u.is_active]
    if not recipients:
        return

    cta_url = f"{config.BASE_URL}/caterer/orders/{order.id}"
    for user in recipients:
        render_and_send_async(
            to=user.email,
            subject="Votre devis a été accepté",
            template_name="order_confirmed",
            user=user,
            caterer=caterer,
            company=company,
            quote_reference=quote.reference,
            event_date=qr.event_date,
            guest_count=qr.guest_count,
            delivery_address=order.delivery_address,
            total_amount_ht=quote.total_amount_ht,
            cta_url=cta_url,
        )
