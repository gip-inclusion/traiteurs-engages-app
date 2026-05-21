from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    Caterer,
    CatererReview,
    Notification,
    Order,
    OrderStatus,
    Quote,
    QuoteRequest,
    User,
)


@dataclass(frozen=True)
class ReviewAggregate:
    avg: Decimal | None  # rounded to 1 decimal; None when count == 0
    count: int


def aggregates_for_caterers(
    db: Session, caterer_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ReviewAggregate]:
    # Caterers without reviews are absent from the dict.
    if not caterer_ids:
        return {}
    rows = db.execute(
        select(
            CatererReview.caterer_id,
            func.avg(CatererReview.rating).label("avg"),
            func.count(CatererReview.id).label("count"),
        )
        .where(CatererReview.caterer_id.in_(caterer_ids))
        .group_by(CatererReview.caterer_id)
    ).all()
    one_decimal = Decimal("0.1")
    return {
        row.caterer_id: ReviewAggregate(
            avg=row.avg.quantize(one_decimal) if row.avg is not None else None,
            count=int(row.count),
        )
        for row in rows
    }


def aggregate_for_caterer(db: Session, caterer_id: uuid.UUID) -> ReviewAggregate:
    return aggregates_for_caterers(db, [caterer_id]).get(
        caterer_id, ReviewAggregate(avg=None, count=0)
    )


def list_for_caterer(
    db: Session, caterer_id: uuid.UUID, *, limit: int | None = None
) -> list[CatererReview]:
    from sqlalchemy.orm import joinedload

    stmt = (
        select(CatererReview)
        .where(CatererReview.caterer_id == caterer_id)
        .options(joinedload(CatererReview.reviewer).joinedload(User.company))
        .order_by(CatererReview.created_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def format_author(reviewer: User | None) -> str:
    # Full anonymisation — the previous "FirstName L. — CompanyName" shape
    # let caterers pin reviewers and competitors profile relationships.
    # The reviewer relationship stays on the row for moderation/audit.
    return "Un client"


class ReviewError(Exception):
    pass


class OrderNotReviewable(ReviewError):
    pass


class InvalidRating(ReviewError):
    pass


def _coerce_rating(raw) -> int:
    # int(3.7) silently truncates; str() round-trip rejects floats.
    if raw is None:
        raise InvalidRating
    try:
        rating = int(str(raw))
    except (TypeError, ValueError) as exc:
        raise InvalidRating from exc
    if rating < 1 or rating > 5:
        raise InvalidRating
    return rating


def _load_reviewable_order(db: Session, *, order_id: uuid.UUID, viewer: User) -> Order:
    # Allowed iff order is paid + viewer is qr.user_id + not already reviewed.
    order = db.get(Order, order_id)
    if order is None or order.status != OrderStatus.paid:
        raise OrderNotReviewable
    quote = db.get(Quote, order.quote_id)
    if quote is None:
        raise OrderNotReviewable
    qr = db.get(QuoteRequest, quote.quote_request_id)
    if qr is None or qr.user_id != viewer.id:
        raise OrderNotReviewable
    if db.scalar(select(CatererReview.id).where(CatererReview.order_id == order_id)):
        raise OrderNotReviewable
    return order


def submit_review(
    db: Session,
    *,
    order_id: uuid.UUID,
    viewer: User,
    rating_raw,
    comment_raw: str | None,
) -> CatererReview:
    rating = _coerce_rating(rating_raw)
    order = _load_reviewable_order(db, order_id=order_id, viewer=viewer)
    quote = db.get(Quote, order.quote_id)
    review = CatererReview(
        caterer_id=quote.caterer_id,
        order_id=order.id,
        reviewer_user_id=viewer.id,
        rating=rating,
        comment=(comment_raw or "").strip() or None,
    )
    db.add(review)
    db.flush()
    return review


def can_review(db: Session, *, order: Order, viewer: User) -> bool:
    try:
        _load_reviewable_order(db, order_id=order.id, viewer=viewer)
    except ReviewError:
        return False
    return True


def notify_review_invite(db: Session, *, order: Order) -> Notification | None:
    # Idempotent: bails out silently on non-paid, already-reviewed, or
    # already-invited (covers webhook redeliveries of invoice.paid).
    if order.status != OrderStatus.paid:
        return None

    quote = db.get(Quote, order.quote_id)
    if quote is None:
        return None
    qr = db.get(QuoteRequest, quote.quote_request_id)
    if qr is None or qr.user_id is None:
        return None

    if db.scalar(select(CatererReview.id).where(CatererReview.order_id == order.id)):
        return None

    duplicate = db.scalar(
        select(Notification.id).where(
            Notification.user_id == qr.user_id,
            Notification.type == "review_invite",
            Notification.related_entity_type == "order",
            Notification.related_entity_id == order.id,
        )
    )
    if duplicate:
        return None

    caterer = db.get(Caterer, quote.caterer_id) if quote.caterer_id else None
    caterer_name = caterer.name if caterer else "le traiteur"
    note = Notification(
        user_id=qr.user_id,
        type="review_invite",
        title="Laissez votre avis",
        body=(
            f"Votre commande avec {caterer_name} est désormais payée. "
            "Vous pouvez maintenant laisser un avis sur le traiteur."
        ),
        related_entity_type="order",
        related_entity_id=order.id,
    )
    db.add(note)
    db.flush()
    return note
