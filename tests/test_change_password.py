"""Tests for the authenticated change-password feature.

The route `/account/change-password` lives in `auth_bp` and is gated by
`login_required` only — no role filter — so the four seeded test users
(super_admin, client_admin, client_user, caterer) must all be able to
rotate their password.

The seeded test password is `testpass`. Mutating tests restore it in a
`finally` block so other tests in the suite still pass.
"""

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


# ---------------------------------------------------------------------------
# Happy path — every role can rotate its own password
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user_email",
    [
        "alice@test.local",  # client_admin
        "bob@test.local",  # client_user
        "cook@test.local",  # caterer
        "admin@test.local",  # super_admin
    ],
)
def test_change_password_works_for_every_role(client, login, user_email):
    """The route is shared by every role — pas de filtre de rôle, juste
    `login_required`. Each of the 4 seeded users must succeed."""
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


# ---------------------------------------------------------------------------
# Rejections — each guard returns 400 and leaves the hash untouched
# ---------------------------------------------------------------------------


def test_change_password_wrong_current_is_rejected(client, login):
    """A wrong current password must block the change — even when the
    new/confirm pair is otherwise valid."""
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
    """New ≠ confirm → 400, hash untouched."""
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
    """`validate_password` exige ≥12 caractères et 3 classes de
    caractères — un mot de passe court doit être refusé."""
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
    """Re-using the same plaintext re-hashes (different bcrypt salt → new
    hash) AND bumps `password_changed_at`, which silently invalidates
    other sessions. Misleading UX — the handler rejects explicitly.

    To hit this branch we need the new password to pass
    `validate_password` first, so we pre-set the account to a strong
    password and try to "change" it to the same value."""
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


# ---------------------------------------------------------------------------
# Auth + session side-effects
# ---------------------------------------------------------------------------


def test_change_password_requires_login(client):
    """Without a session → redirect to /login. Pas d'écran ouvert, pas
    de mutation possible."""
    r = client.get("/account/change-password", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_change_password_bumps_password_changed_at(client, login):
    """A successful change bumps `password_changed_at` — c'est ce champ
    qui, comparé au snapshot de session, déconnecte les autres
    appareils (audit H-5, PR #69)."""
    from sqlalchemy import select

    from database import session_factory
    from models import User

    s = session_factory()
    try:
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))
        before = alice.password_changed_at
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
            after = alice.password_changed_at
        finally:
            s.close()

        assert after is not None
        if before is not None:
            assert after > before
    finally:
        _reset_password("alice@test.local")
