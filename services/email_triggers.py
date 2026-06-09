from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from models import (
    Caterer,
    Message,
    Order,
    QRCStatus,
    Quote,
    QuoteRequest,
    QuoteRequestCaterer,
    User,
    UserRole,
)
from services.email import render_and_send_async


logger = logging.getLogger(__name__)


def _safe(label: str):
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


# --- E1 — Quote request received (caterer side) --------------------------
#
# Audit emails : P0 #1. Sans ce mail, un traiteur ignore qu'une demande
# lui a été transmise tant qu'il ne se reconnecte pas — tout le funnel
# « 3 devis » et le ciblage direct dépendent du fait que le traiteur
# revienne dans son espace pour répondre.


@_safe("quote_request_received")
def quote_request_received(
    db: Session,
    *,
    quote_request: QuoteRequest,
    caterer: Caterer,
) -> None:
    """Email envoyé à chaque user actif du traiteur quand une demande
    lui est attribuée (fan-out 3 devis ou ciblage direct).

    Mêmes garanties que `order_confirmed` : on charge les destinataires
    ici pour ne pas dépendre du caller, et on enqueue 1 mail par user
    (personnalisation + isolement d'un échec à un seul destinataire)."""
    recipients = db.scalars(
        select(User).where(
            User.caterer_id == caterer.id,
            User.is_active.is_(True),
        )
    ).all()
    if not recipients:
        return

    company_name = quote_request.company.name if quote_request.company else ""
    cta_url = f"{config.BASE_URL}/caterer/requests/{quote_request.id}"
    for user in recipients:
        render_and_send_async(
            to=user.email,
            subject="Nouvelle demande de devis",
            template_name="quote_request_received",
            user=user,
            caterer=caterer,
            company_name=company_name,
            event_date=quote_request.event_date,
            event_city=quote_request.event_city,
            guest_count=quote_request.guest_count,
            cta_url=cta_url,
        )


# --- E2 — Message received -----------------------------------------------
#
# Email envoyé au destinataire d'un message dès sa réception, en parallèle
# de la notif in-app (blueprints/api.send_message). Sans cet email, un
# contact ne sait qu'il a reçu un message que s'il revient sur la
# plateforme.

# Préfixe d'URL de la messagerie selon le rôle du destinataire : chaque
# espace a sa propre route `*.message_thread` (cf. blueprints/_messages.py).
_MESSAGE_THREAD_PREFIX = {
    UserRole.client_admin: "/client",
    UserRole.client_user: "/client",
    UserRole.caterer: "/caterer",
    UserRole.super_admin: "/admin",
}

_MESSAGE_PREVIEW_MAX = 500


@_safe("message_received")
def message_received(
    *,
    message: Message,
    sender: User,
    recipient: User,
) -> None:
    """Email au destinataire d'un message. Erreurs silencieuses (_safe) :
    un échec d'envoi ne doit pas bloquer l'enregistrement du message."""
    if recipient is None or not recipient.is_active:
        return
    prefix = _MESSAGE_THREAD_PREFIX.get(recipient.role)
    if prefix is None:
        return

    sender_name = (
        f"{sender.first_name} {sender.last_name}".strip()
        if sender is not None
        else "Un contact"
    )
    body = (message.body or "").strip()
    preview = body[:_MESSAGE_PREVIEW_MAX]
    truncated = len(body) > _MESSAGE_PREVIEW_MAX
    cta_url = f"{config.BASE_URL}{prefix}/messages/{message.thread_id}"
    render_and_send_async(
        to=recipient.email,
        subject="Nouveau message",
        template_name="message_received",
        user=recipient,
        sender_name=sender_name,
        preview=preview,
        truncated=truncated,
        cta_url=cta_url,
    )
