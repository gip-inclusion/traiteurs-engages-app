# Convention: `db` first, everything else kwargs. Aucune fonction ne
# commit. Rejet métier = exception typée (sous-classe de WorkflowError).
# Pas d'import Flask pour rester testable hors contexte HTTP.
from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import func, select

_workflow_logger = logging.getLogger("app.workflow")

from models import (
    Caterer,
    Order,
    OrderStatus,
    QRCStatus,
    Quote,
    QuoteRequest,
    QuoteRequestCaterer,
    QuoteRequestStatus,
    QuoteStatus,
    User,
)
from services.notifications import (
    caterer_user_ids,
    company_admin_user_ids,
    notify,
    notify_users,
)


class WorkflowError(Exception):
    pass


class RequestNotFound(WorkflowError):
    pass


class QuoteNotFound(WorkflowError):
    pass


class QuoteNotAvailable(WorkflowError):
    pass


class QuoteExpired(WorkflowError):
    pass


class QuoteRequestClosed(WorkflowError):
    """3-first-responders rule a fermé ce traiteur (QRC closed)."""


class QuoteRequestNotOpen(WorkflowError):
    """La demande elle-même est sortie de `sent_to_caterers` (distinct
    de QuoteRequestClosed pour différencier le message utilisateur)."""


class OrderNotFound(WorkflowError):
    pass


def refuse_quote(
    db,
    *,
    request_id: uuid.UUID,
    quote_id: uuid.UUID,
    user: User,
    reason: str | None,
) -> None:
    # Si plus aucun devis n'est en `sent`, la demande passe en `quotes_refused`.
    qr = db.execute(
        select(QuoteRequest).where(
            QuoteRequest.id == request_id,
            QuoteRequest.company_id == user.company_id,
        )
    ).scalar_one_or_none()
    if not qr:
        raise RequestNotFound

    quote = db.execute(
        select(Quote).where(
            Quote.id == quote_id,
            Quote.quote_request_id == request_id,
        )
    ).scalar_one_or_none()
    if not quote:
        raise QuoteNotFound

    quote.status = QuoteStatus.refused
    quote.refusal_reason = reason or None

    body = "Votre devis a été refusé."
    if reason:
        body += f" Motif : {reason}"
    notify_users(
        db,
        caterer_user_ids(db, quote.caterer_id),
        type="quote_refused",
        title="Devis refusé",
        body=body,
        related_entity_type="quote_request",
        related_entity_id=request_id,
    )

    remaining = db.execute(
        select(func.count(Quote.id)).where(
            Quote.quote_request_id == request_id,
            Quote.status == QuoteStatus.sent,
        )
    ).scalar_one()

    if remaining == 0:
        qr.status = QuoteRequestStatus.quotes_refused

    _workflow_logger.info(
        "quote_refused",
        extra={
            "event": "quote_refused",
            "quote_request_id": str(request_id),
            "quote_id": str(quote_id),
            "caterer_id": str(quote.caterer_id),
            "company_id": str(qr.company_id),
            "remaining_open_quotes": remaining,
        },
    )


def accept_quote(
    db,
    *,
    request_id: uuid.UUID,
    quote_id: uuid.UUID,
    user: User,
) -> Order:
    # Audit #5 garde-fous: only a `sent` non-expired quote can be accepted.
    # VULN-41: FOR UPDATE serializes concurrent accept_quote so two clicks
    # can't both create an Order; Order.quote_id UNIQUE is a backstop.
    qr = db.execute(
        select(QuoteRequest)
        .where(
            QuoteRequest.id == request_id,
            QuoteRequest.company_id == user.company_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if not qr:
        raise RequestNotFound

    # VULN-32: only qualified requests can be acted on; a draft with stray
    # `sent` quotes would otherwise bypass approve_quote_request.
    if qr.status != QuoteRequestStatus.sent_to_caterers:
        raise QuoteNotAvailable

    accepted = db.execute(
        select(Quote).where(
            Quote.id == quote_id,
            Quote.quote_request_id == request_id,
            Quote.status == QuoteStatus.sent,
        )
    ).scalar_one_or_none()
    if not accepted:
        raise QuoteNotAvailable

    if accepted.valid_until and accepted.valid_until < datetime.date.today():
        raise QuoteExpired

    accepted.status = QuoteStatus.accepted

    others = (
        db.execute(
            select(Quote).where(
                Quote.quote_request_id == request_id,
                Quote.id != accepted.id,
                Quote.status == QuoteStatus.sent,
            )
        )
        .scalars()
        .all()
    )
    for q in others:
        q.status = QuoteStatus.refused
        q.refusal_reason = "Un autre devis a ete accepte."

    order = Order(
        quote_id=accepted.id,
        client_admin_id=user.id,
        status=OrderStatus.confirmed,
        delivery_date=qr.event_date,
        delivery_address=f"{qr.event_address}, {qr.event_zip_code} {qr.event_city}",
    )
    db.add(order)
    db.flush()

    qr.status = QuoteRequestStatus.completed

    # Close losing QRCs so the UI shows « Clôturée » and submit_quote
    # refuses late entries. `rejected` / `closed` are terminal — skip.
    losing_qrcs = (
        db.execute(
            select(QuoteRequestCaterer).where(
                QuoteRequestCaterer.quote_request_id == request_id,
                QuoteRequestCaterer.caterer_id != accepted.caterer_id,
                QuoteRequestCaterer.status.not_in(
                    (QRCStatus.rejected, QRCStatus.closed)
                ),
            )
        )
        .scalars()
        .all()
    )
    for losing_qrc in losing_qrcs:
        losing_qrc.status = QRCStatus.closed

    # Only notify the winner; "un autre traiteur a été choisi" mails to
    # losers would be spammy and not critical for V1.
    notify_users(
        db,
        caterer_user_ids(db, accepted.caterer_id),
        type="quote_accepted",
        title="Devis accepté !",
        body="Votre devis a été retenu. Une commande a été créée.",
        related_entity_type="order",
        related_entity_id=order.id,
    )
    _workflow_logger.info(
        "quote_accepted",
        extra={
            "event": "quote_accepted",
            "quote_request_id": str(request_id),
            "quote_id": str(quote_id),
            "order_id": str(order.id),
            "caterer_id": str(accepted.caterer_id),
            "company_id": str(qr.company_id),
            "losing_caterers": len(losing_qrcs),
        },
    )
    return order


def approve_quote_request(
    db,
    *,
    request_id: uuid.UUID,
) -> list[QuoteRequestCaterer]:
    # Fan-out to every is_validated caterer. Empty catalogue ⇒ stays
    # pending_review (admin handler flashes a warning).
    qr = db.get(QuoteRequest, request_id)
    if not qr:
        raise RequestNotFound

    targets = list(
        db.scalars(select(Caterer).where(Caterer.is_validated.is_(True))).all()
    )

    qrcs: list[QuoteRequestCaterer] = []
    for caterer in targets:
        qrc = QuoteRequestCaterer(
            quote_request_id=qr.id,
            caterer_id=caterer.id,
            status=QRCStatus.selected,
        )
        db.add(qrc)
        qrcs.append(qrc)

    if targets:
        qr.status = QuoteRequestStatus.sent_to_caterers

        # Group users by caterer_id in one query — avoids N+1 caterer_user_ids
        # calls on each admin approval.
        target_ids = [c.id for c in targets]
        users_by_caterer: dict = {}
        for uid, cid in db.execute(
            select(User.id, User.caterer_id).where(
                User.caterer_id.in_(target_ids),
                User.is_active.is_(True),
            )
        ).all():
            users_by_caterer.setdefault(cid, []).append(uid)

        for caterer in targets:
            notify_users(
                db,
                users_by_caterer.get(caterer.id, []),
                type="quote_request_received",
                title="Nouvelle demande de devis",
                body=f"Une demande pour {qr.guest_count or '?'} convives "
                f"({qr.event_city or 'lieu non renseigné'}) vous a été transmise.",
                related_entity_type="quote_request",
                related_entity_id=qr.id,
            )
        if qr.user_id is not None:
            notify(
                db,
                user_id=qr.user_id,
                type="quote_request_approved",
                title="Votre demande a été transmise",
                body=f"Votre demande a été envoyée à {len(targets)} traiteur(s).",
                related_entity_type="quote_request",
                related_entity_id=qr.id,
            )
    db.flush()
    _workflow_logger.info(
        "quote_request_approved",
        extra={
            "event": "quote_request_approved",
            "quote_request_id": str(request_id),
            "company_id": str(qr.company_id),
            "caterers_targeted": len(targets),
        },
    )
    return qrcs


def reject_quote_request(
    db,
    *,
    request_id: uuid.UUID,
    reason: str | None,
) -> None:
    qr = db.get(QuoteRequest, request_id)
    if not qr:
        raise RequestNotFound
    qr.status = QuoteRequestStatus.cancelled
    qr.message_to_caterer = reason or ""

    if qr.user_id is not None:
        body = "Votre demande de devis a été refusée par notre équipe."
        if reason:
            body += f" Motif : {reason}"
        notify(
            db,
            user_id=qr.user_id,
            type="quote_request_rejected",
            title="Demande refusée",
            body=body,
            related_entity_type="quote_request",
            related_entity_id=qr.id,
        )
    _workflow_logger.info(
        "quote_request_rejected",
        extra={
            "event": "quote_request_rejected",
            "quote_request_id": str(request_id),
            "company_id": str(qr.company_id),
            "has_reason": bool(reason),
        },
    )


def submit_quote(
    db,
    *,
    request_id: uuid.UUID,
    quote_id: uuid.UUID,
    caterer: Caterer,
) -> Quote:
    # Règle des 3 premiers répondants : rang 1/2/3 transmis ; le 3e ferme
    # les QRC en `selected` ; la 4e soumission lève QuoteRequestClosed.
    # FOR UPDATE sur la QR sérialise les répondants concurrents.
    qr = db.scalar(
        select(QuoteRequest).where(QuoteRequest.id == request_id).with_for_update()
    )
    if qr is None:
        raise QuoteNotFound
    if qr.status != QuoteRequestStatus.sent_to_caterers:
        raise QuoteRequestNotOpen

    quote = db.scalar(
        select(Quote).where(
            Quote.id == quote_id,
            Quote.caterer_id == caterer.id,
            Quote.quote_request_id == request_id,
            Quote.status == QuoteStatus.draft,
        )
    )
    if not quote:
        raise QuoteNotFound

    qrc = db.scalar(
        select(QuoteRequestCaterer).where(
            QuoteRequestCaterer.quote_request_id == request_id,
            QuoteRequestCaterer.caterer_id == caterer.id,
        )
    )
    if not qrc:
        raise QuoteNotFound

    if qrc.status == QRCStatus.closed:
        raise QuoteRequestClosed

    transmitted = db.scalar(
        select(func.count(QuoteRequestCaterer.id))
        .where(QuoteRequestCaterer.quote_request_id == request_id)
        .where(QuoteRequestCaterer.status == QRCStatus.transmitted_to_client)
    )
    if transmitted >= 3:
        # Self-heal: caller is expected to db.commit() on QuoteRequestClosed
        # so the state stays coherent for the next view.
        qrc.status = QRCStatus.closed
        raise QuoteRequestClosed

    quote.status = QuoteStatus.sent
    qrc.responded_at = datetime.datetime.utcnow()
    qrc.status = QRCStatus.transmitted_to_client
    qrc.response_rank = transmitted + 1
    if transmitted + 1 == 3:
        remaining = db.scalars(
            select(QuoteRequestCaterer)
            .where(QuoteRequestCaterer.quote_request_id == request_id)
            .where(QuoteRequestCaterer.status == QRCStatus.selected)
            .where(QuoteRequestCaterer.caterer_id != caterer.id)
        ).all()
        for r in remaining:
            r.status = QRCStatus.closed

    # Notify the requester for every transmission, not just the third.
    if qr.user_id is not None:
        notify(
            db,
            user_id=qr.user_id,
            type="quote_received",
            title="Nouveau devis reçu",
            body=f"{caterer.name} vient de vous envoyer un devis.",
            related_entity_type="quote",
            related_entity_id=quote.id,
        )

    db.flush()
    _workflow_logger.info(
        "quote_submitted",
        extra={
            "event": "quote_submitted",
            "quote_request_id": str(request_id),
            "quote_id": str(quote_id),
            "caterer_id": str(caterer.id),
            "company_id": str(qr.company_id),
            "response_rank": qrc.response_rank,
            "closes_request": qrc.response_rank == 3,
        },
    )
    return quote


def mark_delivered(
    db,
    *,
    order_id: uuid.UUID,
    caterer: Caterer,
) -> Order:
    # Stripe invoicing trigger reste côté handler pour cette itération.
    order = db.scalar(
        select(Order)
        .join(Quote, Order.quote_id == Quote.id)
        .where(Order.id == order_id)
        .where(Quote.caterer_id == caterer.id)
        .where(Order.status == OrderStatus.confirmed)
        .with_for_update()
    )
    if not order:
        raise OrderNotFound
    order.status = OrderStatus.delivered

    qr = order.quote.quote_request
    notify_users(
        db,
        company_admin_user_ids(db, qr.company_id),
        type="order_delivered",
        title="Commande marquée comme livrée",
        body=f"{caterer.name} a marqué la commande comme livrée.",
        related_entity_type="order",
        related_entity_id=order.id,
    )
    _workflow_logger.info(
        "order_delivered",
        extra={
            "event": "order_delivered",
            "order_id": str(order_id),
            "caterer_id": str(caterer.id),
            "company_id": str(qr.company_id),
        },
    )
    return order
