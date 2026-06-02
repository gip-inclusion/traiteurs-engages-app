from __future__ import annotations

import datetime
import hashlib
import secrets

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from models import MembershipStatus, PasswordResetToken, User
from services.email import render_and_send_async


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


RESET_TOKEN_TTL = datetime.timedelta(hours=1)


class ResetTokenInvalid(Exception):
    pass


def issue_token(db: Session, *, user: User) -> tuple[PasswordResetToken, str]:
    raw = secrets.token_urlsafe(32)
    row = PasswordResetToken(
        user_id=user.id,
        token=_hash_token(raw),
        expires_at=datetime.datetime.utcnow() + RESET_TOKEN_TTL,
    )
    db.add(row)
    db.flush()
    return row, raw


def _invalidate_outstanding_tokens(
    db: Session, *, user_id, now: datetime.datetime
) -> None:
    db.execute(
        PasswordResetToken.__table__.update()
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )


def revoke_all_sessions(db: Session, user: User) -> None:
    now = datetime.datetime.utcnow()
    user.sessions_invalidated_at = now
    _invalidate_outstanding_tokens(db, user_id=user.id, now=now)


def consume_token(db: Session, *, raw_token: str, new_password: str) -> User:
    if not raw_token:
        raise ResetTokenInvalid
    row = db.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.token == _hash_token(raw_token))
        .with_for_update()
    )
    if row is None:
        raise ResetTokenInvalid
    if row.used_at is not None:
        raise ResetTokenInvalid
    if row.expires_at < datetime.datetime.utcnow():
        raise ResetTokenInvalid

    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise ResetTokenInvalid
    if (
        user.sessions_invalidated_at is not None
        and row.created_at is not None
        and row.created_at < user.sessions_invalidated_at
    ):
        raise ResetTokenInvalid
    if user.membership_status in (MembershipStatus.pending, MembershipStatus.rejected):
        raise ResetTokenInvalid

    now = datetime.datetime.utcnow()
    row.used_at = now
    user.password_hash = bcrypt.hashpw(
        new_password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    user.sessions_invalidated_at = now
    _invalidate_outstanding_tokens(db, user_id=user.id, now=now)
    db.flush()
    return user


def kick_off_reset(db: Session, *, email: str) -> None:
    user = db.scalar(select(User).where(User.email == (email or "").lower().strip()))
    blocked_membership = user is not None and user.membership_status in (
        MembershipStatus.pending,
        MembershipStatus.rejected,
    )
    if user is None or not user.is_active or blocked_membership:
        bcrypt.hashpw(b"timing-noise", bcrypt.gensalt())
        return

    _invalidate_outstanding_tokens(db, user_id=user.id, now=datetime.datetime.utcnow())
    _row, raw_token = issue_token(db, user=user)
    reset_url = f"{config.BASE_URL}/reset-password/{raw_token}"

    render_and_send_async(
        to=user.email,
        subject="Réinitialisation de votre mot de passe",
        template_name="password_reset",
        user=user,
        reset_url=reset_url,
        ttl_minutes=int(RESET_TOKEN_TTL.total_seconds() // 60),
    )
