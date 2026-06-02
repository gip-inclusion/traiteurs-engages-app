import datetime as _dt
import uuid

import bcrypt
import pytest


@pytest.fixture
def session(app):
    from database import session_factory

    s = session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _alice(s):
    from sqlalchemy import select

    from models import User

    alice = s.scalar(select(User).where(User.email == "alice@test.local"))
    if alice is None:
        all_emails = [u.email for u in s.scalars(select(User)).all()]
        raise AssertionError(f"alice@test.local not in test DB; saw {all_emails}")
    return alice


def test_issue_token_persists_with_ttl_in_future(session):
    from sqlalchemy import select

    from models import PasswordResetToken
    from services import password_reset as pr

    alice = _alice(session)
    row, raw = pr.issue_token(session, user=alice)
    session.flush()

    assert row.user_id == alice.id
    assert row.used_at is None
    assert row.expires_at > _dt.datetime.utcnow()
    assert row.expires_at <= _dt.datetime.utcnow() + pr.RESET_TOKEN_TTL + _dt.timedelta(
        seconds=1
    )
    persisted = session.scalar(
        select(PasswordResetToken).where(PasswordResetToken.id == row.id)
    )
    assert persisted is not None
    assert persisted.token == pr._hash_token(raw)
    assert persisted.token != raw


def test_issue_token_returns_unique_strings(session):
    from services import password_reset as pr

    alice = _alice(session)
    _row_a, a = pr.issue_token(session, user=alice)
    _row_b, b = pr.issue_token(session, user=alice)
    session.flush()
    assert a != b
    assert len(a) >= 32


def test_consume_token_rotates_password_hash_and_flags_used(session):
    from sqlalchemy import select

    from models import PasswordResetToken
    from services import password_reset as pr

    alice = _alice(session)
    old_hash = alice.password_hash
    row, raw = pr.issue_token(session, user=alice)
    session.flush()

    user = pr.consume_token(
        session,
        raw_token=raw,
        new_password="N3w-Strong-Password!",
    )
    session.flush()

    assert user.id == alice.id
    assert user.password_hash != old_hash
    assert bcrypt.checkpw(b"N3w-Strong-Password!", user.password_hash.encode("utf-8"))
    refreshed = session.scalar(
        select(PasswordResetToken).where(PasswordResetToken.id == row.id)
    )
    assert refreshed.used_at is not None


def test_consume_token_rejects_unknown(session):
    from services import password_reset as pr

    with pytest.raises(pr.ResetTokenInvalid):
        pr.consume_token(
            session,
            raw_token="this-token-does-not-exist",
            new_password="N3w-Strong-Password!",
        )


def test_consume_token_rejects_already_used(session):
    from services import password_reset as pr

    alice = _alice(session)
    row, raw = pr.issue_token(session, user=alice)
    session.flush()
    pr.consume_token(session, raw_token=raw, new_password="N3w-Strong-Password!")
    session.flush()

    with pytest.raises(pr.ResetTokenInvalid):
        pr.consume_token(session, raw_token=raw, new_password="Y3t-Another-Password!")


def test_consume_token_rejects_expired(session):
    from services import password_reset as pr

    alice = _alice(session)
    row, raw = pr.issue_token(session, user=alice)
    row.expires_at = _dt.datetime.utcnow() - _dt.timedelta(minutes=1)
    session.flush()

    with pytest.raises(pr.ResetTokenInvalid):
        pr.consume_token(session, raw_token=raw, new_password="N3w-Strong-Password!")


def test_consume_token_rejects_inactive_user(session):
    from services import password_reset as pr

    alice = _alice(session)
    _row, raw = pr.issue_token(session, user=alice)
    alice.is_active = False
    session.flush()

    with pytest.raises(pr.ResetTokenInvalid):
        pr.consume_token(session, raw_token=raw, new_password="N3w-Strong-Password!")


def test_kick_off_reset_unknown_email_creates_no_row(session):
    from sqlalchemy import func, select

    from models import PasswordResetToken
    from services import password_reset as pr

    before = session.scalar(select(func.count(PasswordResetToken.id)))
    pr.kick_off_reset(session, email=f"nobody-{uuid.uuid4()}@example.invalid")
    session.flush()
    after = session.scalar(select(func.count(PasswordResetToken.id)))
    assert after == before


def test_kick_off_reset_known_email_creates_row(session):
    from sqlalchemy import func, select

    from models import PasswordResetToken
    from services import password_reset as pr

    alice = _alice(session)
    before = session.scalar(
        select(func.count(PasswordResetToken.id)).where(
            PasswordResetToken.user_id == alice.id
        )
    )
    pr.kick_off_reset(session, email=alice.email.upper())
    session.flush()
    after = session.scalar(
        select(func.count(PasswordResetToken.id)).where(
            PasswordResetToken.user_id == alice.id
        )
    )
    assert after == before + 1


def test_kick_off_reset_rejected_member_creates_no_row(session):
    from sqlalchemy import func, select

    from models import MembershipStatus, PasswordResetToken
    from services import password_reset as pr

    alice = _alice(session)
    original_status = alice.membership_status
    try:
        alice.membership_status = MembershipStatus.rejected
        session.flush()
        before = session.scalar(
            select(func.count(PasswordResetToken.id)).where(
                PasswordResetToken.user_id == alice.id,
                PasswordResetToken.used_at.is_(None),
            )
        )
        pr.kick_off_reset(session, email=alice.email)
        session.flush()
        after = session.scalar(
            select(func.count(PasswordResetToken.id)).where(
                PasswordResetToken.user_id == alice.id,
                PasswordResetToken.used_at.is_(None),
            )
        )
        assert after == before, (
            "kick_off_reset must not issue a token for a rejected member"
        )
    finally:
        alice.membership_status = original_status
        session.flush()


def test_forgot_password_post_unknown_email_returns_200(client):
    r = client.post(
        "/forgot-password",
        data={
            "email": f"unknown-{uuid.uuid4()}@example.invalid",
        },
    )
    assert r.status_code == 200


def test_forgot_password_post_known_email_returns_200(client):
    r = client.post(
        "/forgot-password",
        data={
            "email": "alice@test.local",
        },
    )
    assert r.status_code == 200


def test_reset_password_get_with_invalid_token_renders_form(client):
    r = client.get("/reset-password/totally-fake-token")
    assert r.status_code == 200


def test_consume_token_bumps_session_revocation_epoch(session):
    from services import password_reset as pr

    alice = _alice(session)
    before = alice.sessions_invalidated_at
    _row, raw = pr.issue_token(session, user=alice)
    session.flush()
    pr.consume_token(session, raw_token=raw, new_password="N3w-Strong-Password!")
    session.flush()

    assert alice.sessions_invalidated_at is not None
    assert before is None or alice.sessions_invalidated_at > before


def test_session_invalidated_after_password_reset(client):
    import bcrypt as _bcrypt

    from database import session_factory
    from services import password_reset as pr

    try:
        r = client.post(
            "/login",
            data={"email": "alice@test.local", "password": "testpass"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        r = client.get("/client/dashboard", follow_redirects=False)
        assert r.status_code == 200, "alice should be authenticated post-login"

        s = session_factory()
        try:
            alice = _alice(s)
            _row, raw = pr.issue_token(s, user=alice)
            s.flush()
            pr.consume_token(s, raw_token=raw, new_password="Reset-Strong-Password1!")
            s.commit()
        finally:
            s.close()

        r = client.get("/client/dashboard", follow_redirects=False)
        assert r.status_code in (302, 403), (
            f"stale session must be rejected after password reset; got {r.status_code}"
        )
    finally:
        s = session_factory()
        try:
            alice = _alice(s)
            alice.password_hash = _bcrypt.hashpw(
                b"testpass", _bcrypt.gensalt()
            ).decode()
            alice.sessions_invalidated_at = None
            s.commit()
        finally:
            s.close()


def test_consume_token_rejects_token_issued_before_revocation(session):
    from services import password_reset as pr

    alice = _alice(session)
    _row, raw = pr.issue_token(session, user=alice)
    session.flush()
    alice.sessions_invalidated_at = _dt.datetime.utcnow() + _dt.timedelta(seconds=1)
    session.flush()

    with pytest.raises(pr.ResetTokenInvalid):
        pr.consume_token(session, raw_token=raw, new_password="N3w-Strong-Password!")


def test_consume_token_rejects_pending_or_rejected_member(session):
    from models import MembershipStatus
    from services import password_reset as pr

    alice = _alice(session)
    original_status = alice.membership_status
    try:
        alice.membership_status = MembershipStatus.rejected
        _row, raw = pr.issue_token(session, user=alice)
        session.flush()
        with pytest.raises(pr.ResetTokenInvalid):
            pr.consume_token(
                session, raw_token=raw, new_password="N3w-Strong-Password!"
            )
    finally:
        alice.membership_status = original_status
        session.flush()


def test_revoke_all_sessions_bumps_and_burns_outstanding_tokens(session):
    from sqlalchemy import select

    from models import PasswordResetToken
    from services import password_reset as pr

    alice = _alice(session)
    before = alice.sessions_invalidated_at
    _row, _raw = pr.issue_token(session, user=alice)
    session.flush()

    pr.revoke_all_sessions(session, alice)
    session.flush()

    assert alice.sessions_invalidated_at is not None
    if before is not None:
        assert alice.sessions_invalidated_at > before

    outstanding = session.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == alice.id,
            PasswordResetToken.used_at.is_(None),
        )
    ).all()
    assert outstanding == []


def test_kick_off_reset_burns_previous_token(session):
    from sqlalchemy import select

    from models import PasswordResetToken
    from services import password_reset as pr

    alice = _alice(session)
    first_row, _first_raw = pr.issue_token(session, user=alice)
    session.flush()
    assert first_row.used_at is None

    pr.kick_off_reset(session, email=alice.email)
    session.flush()

    refreshed = session.scalar(
        select(PasswordResetToken).where(PasswordResetToken.id == first_row.id)
    )
    assert refreshed.used_at is not None, (
        "kick_off_reset must burn previously-emitted links"
    )
