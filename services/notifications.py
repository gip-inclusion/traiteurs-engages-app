import logging

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from models import Caterer, MembershipStatus, Notification, User, UserRole

_notif_logger = logging.getLogger("app.notifications")


def create_notification(
    session: Session,
    user_id,
    type,
    title,
    body,
    related_entity_type=None,
    related_entity_id=None,
):
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
    )
    session.add(notification)
    return notification


def notify(session: Session, **kwargs):
    """Single-user notification with structured log emission.

    Logging is here (not in `create_notification`) so the per-record
    `notification_created` event isn't duplicated when `notify_users`
    fans out — that path emits a single `notifications_dispatched`
    event with a count instead.
    """
    note = create_notification(session, **kwargs)
    uid = kwargs.get("user_id")
    rid = kwargs.get("related_entity_id")
    _notif_logger.info(
        "notification_created",
        extra={
            "event": "notification_created",
            "notification_type": kwargs.get("type"),
            "recipient_id": str(uid) if uid else None,
            "related_entity_type": kwargs.get("related_entity_type"),
            "related_entity_id": str(rid) if rid else None,
        },
    )
    return note


def notify_users(session: Session, user_ids, **kwargs):
    # Tolerates duplicates and None in user_ids so callers can pass the
    # raw result of a recipient-resolver helper.
    seen = set()
    out = []
    for uid in user_ids:
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append(create_notification(session, user_id=uid, **kwargs))
    rid = kwargs.get("related_entity_id")
    _notif_logger.info(
        "notifications_dispatched",
        extra={
            "event": "notifications_dispatched",
            "notification_type": kwargs.get("type"),
            "recipient_count": len(out),
            "related_entity_type": kwargs.get("related_entity_type"),
            "related_entity_id": str(rid) if rid else None,
        },
    )
    return out


def company_admin_user_ids(session: Session, company_id):
    if company_id is None:
        return []
    return list(
        session.scalars(
            select(User.id).where(
                User.company_id == company_id,
                User.role == UserRole.client_admin,
                User.membership_status == MembershipStatus.active,
                User.is_active.is_(True),
            )
        )
    )


def caterer_user_ids(session: Session, caterer_id):
    if caterer_id is None:
        return []
    return list(
        session.scalars(
            select(User.id).where(
                User.caterer_id == caterer_id,
                User.is_active.is_(True),
            )
        )
    )


def caterer_user_ids_for(session: Session, caterer):
    if caterer is None:
        return []
    return caterer_user_ids(session, caterer.id)


def super_admin_user_ids(session: Session):
    return list(
        session.scalars(
            select(User.id).where(
                User.role == UserRole.super_admin,
                User.is_active.is_(True),
            )
        )
    )


# Anchor the Caterer import (referenced only in caterer_user_ids_for's
# docstring previously) for the linter.
_ = Caterer


def notification_target_url(note, role):
    # Lazy url_for import so this module stays usable outside Flask.
    from flask import url_for

    et = note.related_entity_type
    eid = note.related_entity_id
    if eid is None:
        return None

    if et == "quote_request":
        if role in ("client_admin", "client_user"):
            return url_for("client.request_detail", request_id=eid)
        if role == "caterer":
            return url_for("caterer.request_detail", qr_id=eid)
        if role == "super_admin":
            return url_for("admin.qualification_detail", request_id=eid)
        return None

    if et == "order":
        if role in ("client_admin", "client_user"):
            return url_for("client.order_detail", order_id=eid)
        if role == "caterer":
            return url_for("caterer.order_detail", order_id=eid)
        if role == "super_admin":
            return url_for("admin.order_detail", order_id=eid)
        return None

    if et == "quote":
        # No client-side quote URL; bounce to the parent request page.
        from database import get_db
        from models import Quote

        q = get_db().get(Quote, eid)
        if q is None:
            return None
        if role in ("client_admin", "client_user"):
            return url_for("client.request_detail", request_id=q.quote_request_id)
        if role == "caterer":
            return url_for("caterer.request_detail", qr_id=q.quote_request_id)
        return None

    if et == "user" and role == "client_admin":
        return url_for("client.team")

    if et == "caterer" and role == "super_admin":
        return url_for("admin.caterer_detail", caterer_id=eid)

    if et == "company" and role in ("client_admin", "client_user"):
        return url_for("client.dashboard")

    if et == "message":
        from database import get_db
        from models import Message

        msg = get_db().get(Message, eid)
        if msg is None:
            return None
        if role in ("client_admin", "client_user"):
            return url_for("client.message_thread", thread_id=msg.thread_id)
        if role == "caterer":
            return url_for("caterer.message_thread", thread_id=msg.thread_id)
        if role == "super_admin":
            # No admin thread route yet — fall back to the inbox.
            return url_for("admin.messages")

    return None


def get_unread_count(session: Session, user_id):
    return session.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
    )


def mark_as_read(session: Session, notification_id):
    notification = session.get(Notification, notification_id)
    if notification:
        notification.is_read = True
    return notification


def mark_read_for_entity(session: Session, user_id, entity_type, entity_id):
    # Called from app.py's after_request so the bell-dropdown clears
    # entries the user has already opened by any path.
    if not user_id or not entity_type or not entity_id:
        return 0
    result = session.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
            Notification.related_entity_type == entity_type,
            Notification.related_entity_id == entity_id,
        )
        .values(is_read=True)
    )
    return result.rowcount or 0


def mark_read_by_type(session: Session, user_id, entity_type):
    # Used by list pages whose URL has no entity_id (e.g. /client/team).
    if not user_id or not entity_type:
        return 0
    result = session.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
            Notification.related_entity_type == entity_type,
        )
        .values(is_read=True)
    )
    return result.rowcount or 0


def mark_read_for_entities(session: Session, user_id, entity_type, entity_ids):
    if not user_id or not entity_type or not entity_ids:
        return 0
    result = session.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
            Notification.related_entity_type == entity_type,
            Notification.related_entity_id.in_(list(entity_ids)),
        )
        .values(is_read=True)
    )
    return result.rowcount or 0
