import logging
import uuid

from flask import Blueprint, abort, g, jsonify, request
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

import config
from extensions import csrf, limiter
from blueprints.middleware import login_required, validated_caterer_required
from database import get_db
from models import (
    Caterer,
    Message,
    Notification,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    Quote,
    QuoteRequest,
    QuoteRequestCaterer,
    StripeEvent,
    User,
)
from services.audit import log_admin_action
from services.notifications import (
    caterer_user_ids,
    company_admin_user_ids,
    create_notification,
    get_unread_count,
    mark_as_read,
    notify_users,
)
from services.stripe_service import verify_webhook_signature

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")

# Stripe delivers out of order and retries; a stale invoice.payment_failed
# can land after invoice.paid, so terminal paid states are sticky.
_TERMINAL_PAID_STATES = {PaymentStatus.succeeded, PaymentStatus.refunded}

# Cap enforced server-side (DB column is TEXT). Mirrors the modal's maxlength.
MESSAGE_BODY_MAX = 5000


@api_bp.route("/webhooks/stripe", methods=["POST"])
@csrf.exempt
@limiter.exempt
def stripe_webhook():
    # Audit #1: fail closed without a webhook secret — an empty HMAC key lets
    # anyone forge events.
    if not config.STRIPE_WEBHOOK_SECRET:
        logger.error("STRIPE_WEBHOOK_SECRET is not configured; refusing webhook")
        return jsonify({"error": "webhook not configured"}), 503

    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = verify_webhook_signature(
            payload, sig_header, config.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.warning("Invalid Stripe webhook signature")
        return jsonify({"error": "invalid signature"}), 400

    # Audit #2: stripe.Event isn't a dict subclass, so use subscript access.
    event_id = event["id"]
    event_type = event["type"]
    data_object = event["data"]["object"]

    def _field(obj, key, default=None):
        try:
            return obj[key]
        except (KeyError, TypeError):
            return default

    # Audit C-1: dedup INSERT and business mutations share one transaction.
    # The previous shape committed the dedup row first, so a failed business
    # commit + Stripe retry hit the UNIQUE violation, returned 200, and the
    # payment was permanently lost.
    db = get_db()
    db.add(StripeEvent(id=event_id, event_type=event_type))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        logger.info("Ignoring duplicate Stripe event %s (%s)", event_id, event_type)
        return jsonify({"status": "duplicate"}), 200

    # Audit C-2: wrap so any side-effect raising can't leak an HTML 500 page
    # into the Stripe dashboard — return structured JSON with the event_id.
    try:
        if event_type == "invoice.paid":
            stripe_invoice_id = _field(data_object, "id")
            payment = db.scalar(
                select(Payment).where(Payment.stripe_invoice_id == stripe_invoice_id)
            )
            if payment:
                payment.status = PaymentStatus.succeeded
                payment.stripe_charge_id = _field(data_object, "charge")
                order = db.scalar(select(Order).where(Order.id == payment.order_id))
                if order:
                    order.status = OrderStatus.paid
                    qr = order.quote.quote_request
                    notify_users(
                        db,
                        company_admin_user_ids(db, qr.company_id),
                        type="order_paid",
                        title="Paiement enregistré",
                        body="Le paiement de votre commande a été enregistré. Merci !",
                        related_entity_type="order",
                        related_entity_id=order.id,
                    )
                    notify_users(
                        db,
                        caterer_user_ids(db, order.quote.caterer_id),
                        type="order_paid",
                        title="Paiement reçu",
                        body="Le paiement de la commande a été reçu et sera viré sous peu.",
                        related_entity_type="order",
                        related_entity_id=order.id,
                    )
                    # Idempotent: helper skips on retry/redelivery.
                    from services.reviews import notify_review_invite

                    notify_review_invite(db, order=order)

        elif event_type == "invoice.payment_failed":
            stripe_invoice_id = _field(data_object, "id")
            payment = db.scalar(
                select(Payment).where(Payment.stripe_invoice_id == stripe_invoice_id)
            )
            if payment and payment.status not in _TERMINAL_PAID_STATES:
                payment.status = PaymentStatus.failed
            elif payment:
                logger.warning(
                    "Ignoring stale invoice.payment_failed for payment %s (current status: %s)",
                    payment.id,
                    payment.status,
                )

        elif event_type == "account.updated":
            account_id = _field(data_object, "id")
            caterer = db.scalar(
                select(Caterer).where(Caterer.stripe_account_id == account_id)
            )
            if caterer:
                caterer.stripe_charges_enabled = _field(
                    data_object, "charges_enabled", False
                )
                caterer.stripe_payouts_enabled = _field(
                    data_object, "payouts_enabled", False
                )

        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Stripe webhook handler failed",
            extra={"event_id": event_id, "event_type": event_type},
        )
        # JSON 500 (not HTML): Stripe shows the body in its dashboard, and
        # the event_id lets ops correlate with the rolled-back transaction.
        return jsonify({"error": "internal", "event_id": event_id}), 500

    return jsonify({"status": "ok"}), 200


@api_bp.route("/messages/<uuid:thread_id>")
@login_required
@validated_caterer_required
def get_messages(thread_id):
    user = g.current_user
    db = get_db()
    # super_admin reads only threads it actually participates in — it's not
    # a platform-wide observer.
    messages = db.scalars(
        select(Message)
        .where(Message.thread_id == thread_id)
        .where(or_(Message.sender_id == user.id, Message.recipient_id == user.id))
        .options(joinedload(Message.sender))
        .order_by(Message.created_at.asc())
    ).all()

    db.execute(
        Message.__table__.update()
        .where(
            Message.thread_id == thread_id,
            Message.recipient_id == user.id,
            Message.is_read.is_(False),
        )
        .values(is_read=True)
    )
    db.commit()

    result = []
    for msg in messages:
        sender = msg.sender
        result.append(
            {
                "id": str(msg.id),
                "thread_id": str(msg.thread_id),
                "sender_id": str(msg.sender_id),
                "recipient_id": str(msg.recipient_id),
                "sender_name": f"{sender.first_name} {sender.last_name}"
                if sender
                else "Inconnu",
                "body": msg.body,
                "is_read": msg.is_read,
                "created_at": msg.created_at.isoformat(),
            }
        )
    return jsonify({"messages": result})


def _allowed_recipients_for(db, user, *, order_id=None, quote_request_id=None):
    # VULN-04 strict membership: order context → client company users + the
    # assigned caterer's users; QR context → client company users + every
    # solicited caterer's users (so a caterer who hasn't quoted can still
    # ask questions). Empty set means caller isn't a party. Self excluded.
    qr_id = quote_request_id
    caterer_ids: set[uuid.UUID] = set()
    company_id: uuid.UUID | None = None

    if order_id:
        order = db.get(Order, order_id)
        if not order:
            return set()
        quote = db.get(Quote, order.quote_id)
        if not quote:
            return set()
        qr_id = quote.quote_request_id
        caterer_ids.add(quote.caterer_id)

    if qr_id:
        qr = db.get(QuoteRequest, qr_id)
        if not qr:
            return set()
        company_id = qr.company_id
        # Include every solicited caterer (not only those who quoted) so a
        # caterer reviewing a brief can still message the client.
        qrc_caterer_ids = db.scalars(
            select(QuoteRequestCaterer.caterer_id).where(
                QuoteRequestCaterer.quote_request_id == qr_id
            )
        ).all()
        caterer_ids.update(qrc_caterer_ids)

    # Caller must be a party; otherwise probing recipient IDs would enumerate
    # company/caterer membership.
    user_in_company = bool(company_id and user.company_id == company_id)
    user_in_caterers = bool(user.caterer_id and user.caterer_id in caterer_ids)
    if not (user_in_company or user_in_caterers):
        return set()

    # Split by side: a caterer can reach the client but NOT the competitors
    # solicited on the same QR. The previous shape merged both sets so
    # caterer A could DM caterer B about a shared QR.
    allowed: set[uuid.UUID] = set()
    if user_in_company:
        if caterer_ids:
            allowed.update(
                db.scalars(
                    select(User.id).where(User.caterer_id.in_(caterer_ids))
                ).all()
            )
        if company_id:
            allowed.update(
                db.scalars(select(User.id).where(User.company_id == company_id)).all()
            )
    elif user_in_caterers:
        if company_id:
            allowed.update(
                db.scalars(select(User.id).where(User.company_id == company_id)).all()
            )
        # Same-caterer teammates only — competitors stay out of reach.
        allowed.update(
            db.scalars(select(User.id).where(User.caterer_id == user.caterer_id)).all()
        )
    allowed.discard(user.id)
    return allowed


@api_bp.route("/messages", methods=["POST"])
@login_required
@validated_caterer_required
@limiter.limit("60 per minute")
def send_message():
    user = g.current_user
    data = request.get_json() or {}
    try:
        recipient_id = uuid.UUID(str(data.get("recipient_id", "")))
    except (ValueError, TypeError):
        abort(400)
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Le message ne peut pas etre vide."}), 400
    if len(body) > MESSAGE_BODY_MAX:
        return jsonify(
            {"error": f"Le message ne peut pas depasser {MESSAGE_BODY_MAX} caracteres."}
        ), 400

    order_id = None
    if data.get("order_id"):
        try:
            order_id = uuid.UUID(str(data["order_id"]))
        except (ValueError, TypeError):
            order_id = None
    quote_request_id = None
    if data.get("quote_request_id"):
        try:
            quote_request_id = uuid.UUID(str(data["quote_request_id"]))
        except (ValueError, TypeError):
            quote_request_id = None

    db = get_db()
    existing = db.scalar(
        select(Message.thread_id)
        .where(
            or_(
                and_(
                    Message.sender_id == user.id, Message.recipient_id == recipient_id
                ),
                and_(
                    Message.sender_id == recipient_id, Message.recipient_id == user.id
                ),
            )
        )
        .limit(1)
    )
    thread_id = existing if existing else uuid.uuid4()

    is_admin = user.role == "super_admin"
    recipient = db.get(User, recipient_id)
    if recipient is None or not recipient.is_active:
        return jsonify({"error": "Destinataire introuvable ou inactif."}), 404

    # VULN-04: re-validate the business relationship on every send so a
    # stale thread doesn't outlive the QR/order/membership that authorised
    # it. super_admin senders and writes TO a designated support inbox
    # skip the gate. Without explicit order/QR ids, inherit every distinct
    # context from the thread's history and allow if any still resolves.
    recipient_is_support = recipient.role == "super_admin" and (
        not config.SUPPORT_USER_EMAILS
        or recipient.email.lower() in config.SUPPORT_USER_EMAILS
    )
    if not is_admin and not recipient_is_support:
        if recipient.role == "super_admin":
            # Non-support super_admins belong to no company/caterer, so no
            # context could ever allow them. Reject directly instead of
            # hinting at a missing context.
            return jsonify({"error": "Destinataire non autorise."}), 403
        gate_contexts: list[tuple] = []
        if order_id or quote_request_id:
            gate_contexts.append((order_id, quote_request_id))
        elif existing is not None:
            gate_contexts = [
                (oid, qrid)
                for oid, qrid in db.execute(
                    select(Message.order_id, Message.quote_request_id)
                    .where(Message.thread_id == existing)
                    .where(
                        or_(
                            Message.order_id.is_not(None),
                            Message.quote_request_id.is_not(None),
                        )
                    )
                    .distinct()
                ).all()
            ]

        if not gate_contexts:
            return jsonify(
                {
                    "error": "Le message doit etre lie a une commande ou une demande de devis."
                }
            ), 400

        if not any(
            recipient_id
            in _allowed_recipients_for(db, user, order_id=oid, quote_request_id=qrid)
            for oid, qrid in gate_contexts
        ):
            return jsonify({"error": "Destinataire non autorise."}), 403

    msg = Message(
        thread_id=thread_id,
        sender_id=user.id,
        recipient_id=recipient_id,
        order_id=order_id,
        quote_request_id=quote_request_id,
        body=body,
    )
    db.add(msg)
    db.flush()

    logger.info(
        "message_sent",
        extra={
            "event": "message_sent",
            "message_id": str(msg.id),
            "thread_id": str(thread_id),
            "sender_id": str(user.id),
            "recipient_id": str(recipient_id),
            "order_id": str(order_id) if order_id else None,
            "quote_request_id": str(quote_request_id) if quote_request_id else None,
            "body_length": len(body),
        },
    )

    # Audit admin → user messages (support touches, qualification,
    # escalation). Admin↔admin chatter is internal noise.
    if is_admin and recipient.role != "super_admin":
        log_admin_action(
            db,
            user,
            "message.admin_send",
            target_type="user",
            target_id=recipient_id,
            extra={
                "thread_id": str(thread_id),
                "body_length": len(body),
                "order_id": str(order_id) if order_id else None,
                "quote_request_id": str(quote_request_id) if quote_request_id else None,
            },
        )

    create_notification(
        db,
        user_id=recipient_id,
        type="new_message",
        title="Nouveau message",
        body=f"{user.first_name} {user.last_name} vous a envoye un message.",
        related_entity_type="message",
        related_entity_id=msg.id,
    )
    db.commit()

    return jsonify({"status": "ok", "thread_id": str(thread_id)}), 201


@api_bp.route("/notifications")
@login_required
@validated_caterer_required
def get_notifications():
    user = g.current_user
    db = get_db()
    count = get_unread_count(db, user.id)
    notifications = db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
        .order_by(Notification.created_at.desc())
        .limit(20)
    ).all()
    result = [
        {
            "id": str(n.id),
            "type": n.type,
            "title": n.title,
            "body": n.body,
            "created_at": n.created_at.isoformat(),
        }
        for n in notifications
    ]
    return jsonify({"unread_count": count, "notifications": result})


@api_bp.route("/notifications/<uuid:notification_id>/read", methods=["POST"])
@login_required
@validated_caterer_required
def notification_read(notification_id):
    user = g.current_user
    db = get_db()
    notification = db.get(Notification, notification_id)
    if not notification or notification.user_id != user.id:
        return jsonify({"error": "Non trouve."}), 404
    mark_as_read(db, notification_id)
    db.commit()
    return jsonify({"status": "ok"})
