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


def _invalidate_outstanding_tokens(db: Session, *, user_id, now: datetime.datetime) -> None:
    # Bulk SET used_at=now on every outstanding token for this user. Used
    # both when revoking and when (re)issuing so only the freshest token
    # is ever redeemable.
    db.execute(
        PasswordResetToken.__table__.update()
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )


def revoke_all_sessions(db: Session, user: User) -> None:
    # Centralised revocation: bumps password_changed_at (load_current_user
    # then evicts every existing cookie) AND marks outstanding reset
    # tokens as used so an in-flight /forgot-password link can't be
    # cashed in afterwards. Caller commits.
    now = datetime.datetime.utcnow()
    user.password_changed_at = now
    _invalidate_outstanding_tokens(db, user_id=user.id, now=now)


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
    # Defence in depth: reject a token issued before the last revocation
    # of the user (membership revoked, password changed elsewhere, email
    # rotated, etc.). Mirrors load_current_user's snapshot check.
    if (
        user.password_changed_at is not None
        and row.created_at is not None
        and row.created_at < user.password_changed_at
    ):
        raise ResetTokenInvalid
    # Audit H-2 mirror: a rejected/pending account must not be able to
    # complete a reset and "succeed" in a misleading way.
    from models import MembershipStatus

    if user.membership_status in (MembershipStatus.pending, MembershipStatus.rejected):
        raise ResetTokenInvalid

    now = datetime.datetime.utcnow()
    row.used_at = now
    user.password_hash = bcrypt.hashpw(
        new_password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    # Bumping invalidates every existing session (see load_current_user).
    user.password_changed_at = now
    # Burn any other in-flight tokens for this user (attacker-issued
    # parallel reset can't be cashed in after the legitimate one).
    _invalidate_outstanding_tokens(db, user_id=user.id, now=now)
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

    # Single live token per user: a fresh /forgot-password burns any
    # previously-emitted link so only the most recent recipient can reset.
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
