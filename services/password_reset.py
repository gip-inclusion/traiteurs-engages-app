from __future__ import annotations

import datetime
import hashlib
import secrets

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from models import PasswordResetToken, User
from services.email import render_and_send_async


def _hash_token(raw: str) -> str:
    # Only the digest lives in the DB; a DB leak doesn't yield reusable tokens.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


RESET_TOKEN_TTL = datetime.timedelta(hours=1)


class ResetTokenInvalid(Exception):
    """One opaque exception for unknown / used / expired so handlers
    can't accidentally distinguish them to a brute-forcer."""


def issue_token(db: Session, *, user: User) -> tuple[PasswordResetToken, str]:
    # Callers MUST use raw_token in the link, never row.token (digest).
    raw = secrets.token_urlsafe(32)
    row = PasswordResetToken(
        user_id=user.id,
        token=_hash_token(raw),
        expires_at=datetime.datetime.utcnow() + RESET_TOKEN_TTL,
    )
    db.add(row)
    db.flush()
    return row, raw


def consume_token(db: Session, *, raw_token: str, new_password: str) -> User:
    if not raw_token:
        raise ResetTokenInvalid
    # FOR UPDATE serializes concurrent redemption attempts.
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

    now = datetime.datetime.utcnow()
    row.used_at = now
    user.password_hash = bcrypt.hashpw(
        new_password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    # Bumping invalidates every existing session (see load_current_user).
    user.password_changed_at = now
    db.flush()
    return user


def kick_off_reset(db: Session, *, email: str) -> None:
    # Always returns None and the caller renders the same success page so
    # account existence isn't leaked. Dummy bcrypt on the no-match path
    # tracks the timing of the real path.
    user = db.scalar(select(User).where(User.email == (email or "").lower().strip()))
    if user is None or not user.is_active:
        bcrypt.hashpw(b"timing-noise", bcrypt.gensalt())
        return

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
