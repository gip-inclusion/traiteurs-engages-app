import bcrypt
import pytest


def _user_password_hash(email):
    from sqlalchemy import select

    from database import session_factory
    from models import User

    s = session_factory()
    try:
        return s.scalar(select(User).where(User.email == email)).password_hash
    finally:
        s.close()


def _reset_password(email, plain="testpass"):
    from sqlalchemy import select

    from database import session_factory
    from models import User

    s = session_factory()
    try:
        user = s.scalar(select(User).where(User.email == email))
        user.password_hash = bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
        s.commit()
    finally:
        s.close()


@pytest.mark.parametrize(
    "user_email",
    [
        "alice@test.local",
        "bob@test.local",
        "cook@test.local",
        "admin@test.local",
    ],
)
def test_change_password_works_for_every_role(client, login, user_email):
    try:
        login(user_email)
        r = client.post(
            "/account/change-password",
            data={
                "current_password": "testpass",
                "new_password": "BrandNewPass123!",
                "new_password_confirm": "BrandNewPass123!",
            },
        )
        assert r.status_code == 302, r.data
        h = _user_password_hash(user_email)
        assert bcrypt.checkpw(b"BrandNewPass123!", h.encode()), (
            "the new password must verify against the stored hash"
        )
        assert not bcrypt.checkpw(b"testpass", h.encode()), (
            "the old password must no longer verify"
        )
    finally:
        _reset_password(user_email)


def test_change_password_wrong_current_is_rejected(client, login):
    try:
        login("alice@test.local")
        r = client.post(
            "/account/change-password",
            data={
                "current_password": "wrong-current",
                "new_password": "BrandNewPass123!",
                "new_password_confirm": "BrandNewPass123!",
            },
        )
        assert r.status_code == 400
        assert bcrypt.checkpw(
            b"testpass", _user_password_hash("alice@test.local").encode()
        )
    finally:
        _reset_password("alice@test.local")


def test_change_password_mismatched_confirm_is_rejected(client, login):
    try:
        login("alice@test.local")
        r = client.post(
            "/account/change-password",
            data={
                "current_password": "testpass",
                "new_password": "BrandNewPass123!",
                "new_password_confirm": "DifferentPass456?",
            },
        )
        assert r.status_code == 400
        assert bcrypt.checkpw(
            b"testpass", _user_password_hash("alice@test.local").encode()
        )
    finally:
        _reset_password("alice@test.local")


def test_change_password_weak_new_is_rejected(client, login):
    try:
        login("alice@test.local")
        r = client.post(
            "/account/change-password",
            data={
                "current_password": "testpass",
                "new_password": "short",
                "new_password_confirm": "short",
            },
        )
        assert r.status_code == 400
        assert bcrypt.checkpw(
            b"testpass", _user_password_hash("alice@test.local").encode()
        )
    finally:
        _reset_password("alice@test.local")


def test_change_password_same_as_current_is_rejected(client, login):
    _reset_password("alice@test.local", plain="ValidPass123!")
    try:
        login("alice@test.local", password="ValidPass123!")
        r = client.post(
            "/account/change-password",
            data={
                "current_password": "ValidPass123!",
                "new_password": "ValidPass123!",
                "new_password_confirm": "ValidPass123!",
            },
        )
        assert r.status_code == 400
        assert bcrypt.checkpw(
            b"ValidPass123!", _user_password_hash("alice@test.local").encode()
        )
    finally:
        _reset_password("alice@test.local")


def test_change_password_requires_login(client):
    r = client.get("/account/change-password", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_change_password_bumps_session_revocation_epoch(client, login):
    from sqlalchemy import select

    from database import session_factory
    from models import User

    s = session_factory()
    try:
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))
        before = alice.sessions_invalidated_at
    finally:
        s.close()

    try:
        login("alice@test.local")
        r = client.post(
            "/account/change-password",
            data={
                "current_password": "testpass",
                "new_password": "FreshPass789!",
                "new_password_confirm": "FreshPass789!",
            },
        )
        assert r.status_code == 302

        s = session_factory()
        try:
            alice = s.scalar(select(User).where(User.email == "alice@test.local"))
            after = alice.sessions_invalidated_at
        finally:
            s.close()

        assert after is not None
        if before is not None:
            assert after > before
    finally:
        _reset_password("alice@test.local")
