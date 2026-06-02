from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from flask import url_for

from models import Message, User, UserRole


def _avatar_for_user(other_user) -> tuple[str | None, str]:
    if other_user is None:
        return None, "unknown"
    if other_user.role == UserRole.caterer and other_user.caterer:
        return other_user.caterer.logo_url, "caterer"
    if other_user.company:
        return other_user.company.logo_url, "client"
    return None, "unknown"


def _entity_name(other_user) -> str:
    if other_user is None:
        return "Inconnu"
    if other_user.role == UserRole.caterer and other_user.caterer:
        return other_user.caterer.name
    if other_user.company:
        return other_user.company.name
    return f"{other_user.first_name} {other_user.last_name}"


def detail_url_for(viewer, other_user) -> str | None:
    if other_user is None:
        return None
    if viewer.role == UserRole.super_admin:
        if other_user.role == UserRole.caterer and other_user.caterer:
            return url_for("admin.caterer_detail", caterer_id=other_user.caterer.id)
        if other_user.company:
            return url_for("admin.company_detail", company_id=other_user.company.id)
        return None
    if viewer.role in (UserRole.client_admin, UserRole.client_user):
        if other_user.role == UserRole.caterer and other_user.caterer:
            return url_for("client.caterer_detail", caterer_id=other_user.caterer.id)
        return None
    return None


def _summarise_thread(
    *,
    viewer_id,
    other_user,
    last_message: Message,
    unread: int,
) -> dict:
    avatar_url, avatar_kind = _avatar_for_user(other_user)
    return {
        "thread_id": str(last_message.thread_id),
        "other_user_id": str(other_user.id) if other_user else None,
        "other_name": _entity_name(other_user),
        "other_role": str(other_user.role) if other_user else "unknown",
        "other_avatar_url": avatar_url,
        "other_avatar_kind": avatar_kind,
        "last_message": (last_message.body or "")[:80],
        "last_at": last_message.created_at,
        "unread": unread,
    }


def threads_for_viewer(db: Session, viewer) -> list[dict]:
    last_messages = db.scalars(
        select(Message)
        .where(or_(Message.sender_id == viewer.id, Message.recipient_id == viewer.id))
        .order_by(Message.thread_id, Message.created_at.desc())
        .distinct(Message.thread_id)
    ).all()
    if not last_messages:
        return []

    last_messages.sort(key=lambda m: m.created_at, reverse=True)

    unread_by_thread = dict(
        db.execute(
            select(Message.thread_id, func.count(Message.id))
            .where(
                Message.recipient_id == viewer.id,
                Message.is_read.is_(False),
            )
            .group_by(Message.thread_id)
        ).all()
    )

    other_ids = {
        msg.recipient_id if msg.sender_id == viewer.id else msg.sender_id
        for msg in last_messages
    }
    other_ids.discard(None)
    users_by_id = (
        {u.id: u for u in db.scalars(select(User).where(User.id.in_(other_ids))).all()}
        if other_ids
        else {}
    )

    return [
        _summarise_thread(
            viewer_id=viewer.id,
            other_user=users_by_id.get(
                msg.recipient_id if msg.sender_id == viewer.id else msg.sender_id
            ),
            last_message=msg,
            unread=unread_by_thread.get(msg.thread_id, 0),
        )
        for msg in last_messages
    ]


def active_thread_context(db: Session, *, thread_id, viewer) -> dict | None:
    first_msg = db.scalar(
        select(Message)
        .where(Message.thread_id == thread_id)
        .where(or_(Message.sender_id == viewer.id, Message.recipient_id == viewer.id))
    )
    if not first_msg:
        return None

    other_id = (
        first_msg.recipient_id
        if first_msg.sender_id == viewer.id
        else first_msg.sender_id
    )
    other_user = db.get(User, other_id) if other_id else None
    if other_user is None:
        return None

    avatar_url, avatar_kind = _avatar_for_user(other_user)
    contact_full_name = (
        f"{other_user.first_name} {other_user.last_name}".strip()
        if other_user.first_name or other_user.last_name
        else ""
    )
    return {
        "other_user_id": str(other_user.id),
        "other_name": _entity_name(other_user),
        "other_role": str(other_user.role),
        "other_avatar_url": avatar_url,
        "other_avatar_kind": avatar_kind,
        "contact_full_name": contact_full_name,
        "detail_url": detail_url_for(viewer, other_user),
    }
