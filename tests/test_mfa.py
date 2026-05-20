"""Tests for MFA TOTP enrollment, verification, and disable flows.

Covers RGS-AUTH.2 (multi-factor for privileged access). The fixtures
manipulate the seeded admin@test.local user directly via SQLAlchemy
because the enrollment dance reaches into the session between the
GET (display) and POST (verify) — easier to bypass and assert the
post-state than to round-trip the QR.
"""

from __future__ import annotations

import pyotp
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker


def _session():
    from database import engine

    Session = sessionmaker(bind=engine)
    return Session()


def _get_user(email: str):
    from models import User

    s = _session()
    try:
        return s.scalar(select(User).where(User.email == email))
    finally:
        s.close()


def _reset_admin_mfa():
    """Wipe MFA state on the seeded admin so test order doesn't matter."""
    from models import User

    s = _session()
    try:
        admin = s.scalar(select(User).where(User.email == "admin@test.local"))
        admin.mfa_secret = None
        admin.mfa_enabled = False
        admin.mfa_recovery_codes = None
        admin.mfa_enrolled_at = None
        s.commit()
    finally:
        s.close()


def _enroll_admin_with(secret: str, recovery_codes_plain: list[str]):
    """Programmatically enrol admin@test.local with a known secret and codes."""
    from services import mfa as mfa_service
    from models import User
    import datetime

    s = _session()
    try:
        admin = s.scalar(select(User).where(User.email == "admin@test.local"))
        admin.mfa_secret = mfa_service.encrypt_secret(secret)
        admin.mfa_recovery_codes = mfa_service.hash_recovery_codes(recovery_codes_plain)
        admin.mfa_enabled = True
        admin.mfa_enrolled_at = datetime.datetime.utcnow()
        s.commit()
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _reset_mfa_between_tests():
    _reset_admin_mfa()
    yield
    _reset_admin_mfa()


def _login(client, email: str, password: str):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------


def test_super_admin_without_mfa_is_forced_to_setup(client):
    _login(client, "admin@test.local", "testpass")
    resp = client.get("/admin/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin/security/mfa/setup" in resp.headers["Location"]


def test_other_roles_are_not_forced_to_setup(client):
    _login(client, "alice@test.local", "testpass")
    resp = client.get("/client/dashboard", follow_redirects=False)
    assert resp.status_code == 200


def test_setup_get_renders_qr(client):
    _login(client, "admin@test.local", "testpass")
    resp = client.get("/admin/security/mfa/setup")
    assert resp.status_code == 200
    assert b"data:image/png;base64," in resp.data


def test_setup_post_with_invalid_code_does_not_enable(client):
    _login(client, "admin@test.local", "testpass")
    client.get("/admin/security/mfa/setup")  # mint secret into session
    resp = client.post(
        "/admin/security/mfa/setup",
        data={"code": "000000"},
        follow_redirects=False,
    )
    # Re-rendering the setup page, not redirecting to recovery codes.
    assert resp.status_code == 200
    admin = _get_user("admin@test.local")
    assert admin.mfa_enabled is False


def test_setup_post_with_valid_code_enables_and_returns_codes(client):
    _login(client, "admin@test.local", "testpass")
    client.get("/admin/security/mfa/setup")
    with client.session_transaction() as sess:
        secret = sess["mfa_setup_secret"]
    code = pyotp.TOTP(secret).now()
    resp = client.post(
        "/admin/security/mfa/setup",
        data={"code": code},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/admin/security/mfa/recovery-codes" in resp.headers["Location"]
    admin = _get_user("admin@test.local")
    assert admin.mfa_enabled is True
    assert admin.mfa_secret is not None
    assert len(admin.mfa_recovery_codes) == 10


def test_setup_refuses_re_enrollment_when_already_enabled(client):
    """Regression: a POST to /admin/security/mfa/setup from a user with
    MFA already enabled must NOT mint a fresh secret or overwrite the
    existing one. The only legitimate re-enrollment path is
    disable-then-enroll (which requires password + current TOTP).
    """
    from services import mfa as mfa_service

    original_secret = pyotp.random_base32()
    _enroll_admin_with(original_secret, ["AAAA-1111"] * 10)
    _login(client, "admin@test.local", "testpass")
    # Clear the MFA gate so we have a fully authenticated admin session,
    # which is the realistic attacker prerequisite (stolen session cookie).
    client.post("/mfa/verify", data={"code": pyotp.TOTP(original_secret).now()})

    # POST with empty code — must NOT trigger a fresh QR render or
    # overwrite the secret. Should land on the status page.
    resp = client.post(
        "/admin/security/mfa/setup",
        data={"code": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    # Status template, not the setup template — no fresh QR rendered.
    assert b"data:image/png;base64," not in resp.data
    # Session must not contain a stale enrollment secret.
    with client.session_transaction() as sess:
        assert "mfa_setup_secret" not in sess

    admin = _get_user("admin@test.local")
    # Secret unchanged — the original one still matches.
    assert mfa_service.decrypt_secret(admin.mfa_secret) == original_secret
    assert admin.mfa_enabled is True
    # Recovery codes unchanged: same 10 hashes, all still unused.
    assert len(admin.mfa_recovery_codes) == 10
    assert all(c["used_at"] is None for c in admin.mfa_recovery_codes)


# ---------------------------------------------------------------------------
# Login + verify
# ---------------------------------------------------------------------------


def test_login_with_mfa_redirects_to_verify_without_authenticating(client):
    secret = pyotp.random_base32()
    _enroll_admin_with(secret, ["ABCD-1234"] * 10)

    resp = _login(client, "admin@test.local", "testpass")
    assert resp.status_code == 302
    assert "/mfa/verify" in resp.headers["Location"]

    # Partial session only — protected admin pages should still bounce.
    resp2 = client.get("/admin/dashboard", follow_redirects=False)
    assert resp2.status_code == 302
    assert "/mfa/verify" in resp2.headers["Location"]


def test_mfa_verify_with_valid_totp_completes_login(client):
    secret = pyotp.random_base32()
    _enroll_admin_with(secret, ["ABCD-1234"] * 10)
    _login(client, "admin@test.local", "testpass")

    code = pyotp.TOTP(secret).now()
    resp = client.post(
        "/mfa/verify",
        data={"code": code},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/admin/dashboard" in resp.headers["Location"]


def test_mfa_verify_with_invalid_totp_keeps_partial_session(client):
    secret = pyotp.random_base32()
    _enroll_admin_with(secret, ["ABCD-1234"] * 10)
    _login(client, "admin@test.local", "testpass")

    resp = client.post("/mfa/verify", data={"code": "000000"})
    # Re-renders the verify page — still on partial session.
    assert resp.status_code == 200
    # No full session yet
    with client.session_transaction() as sess:
        assert "user_id" not in sess
        assert sess.get("mfa_pending_user_id") is not None


def test_mfa_verify_with_recovery_code_completes_and_consumes(client):
    secret = pyotp.random_base32()
    codes = ["AAAA-1111", "BBBB-2222", "CCCC-3333"] + ["ZZZZ-9999"] * 7
    _enroll_admin_with(secret, codes)
    _login(client, "admin@test.local", "testpass")

    resp = client.post(
        "/mfa/verify?mode=recovery",
        data={"code": "BBBB-2222", "mode": "recovery"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/admin/dashboard" in resp.headers["Location"]

    # Same code rejected on a second use.
    admin = _get_user("admin@test.local")
    used = [c for c in admin.mfa_recovery_codes if c.get("used_at") is not None]
    assert len(used) == 1


# ---------------------------------------------------------------------------
# Disable + recovery code regeneration
# ---------------------------------------------------------------------------


def test_disable_requires_both_password_and_totp(client):
    secret = pyotp.random_base32()
    _enroll_admin_with(secret, ["ABCD-1234"] * 10)
    _login(client, "admin@test.local", "testpass")
    code = pyotp.TOTP(secret).now()
    client.post("/mfa/verify", data={"code": code})

    # Wrong password — refused
    resp = client.post(
        "/admin/security/mfa/disable",
        data={"password": "wrong", "code": pyotp.TOTP(secret).now()},
        follow_redirects=False,
    )
    assert resp.status_code == 302  # redirect back to setup
    admin = _get_user("admin@test.local")
    assert admin.mfa_enabled is True

    # Wrong code — refused
    resp = client.post(
        "/admin/security/mfa/disable",
        data={"password": "testpass", "code": "000000"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    admin = _get_user("admin@test.local")
    assert admin.mfa_enabled is True

    # Both valid — accepted
    resp = client.post(
        "/admin/security/mfa/disable",
        data={"password": "testpass", "code": pyotp.TOTP(secret).now()},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    admin = _get_user("admin@test.local")
    assert admin.mfa_enabled is False
    assert admin.mfa_secret is None
    assert admin.mfa_recovery_codes is None


def test_regenerate_recovery_codes_requires_totp(client):
    secret = pyotp.random_base32()
    _enroll_admin_with(secret, ["AAAA-1111"] * 10)
    _login(client, "admin@test.local", "testpass")
    client.post("/mfa/verify", data={"code": pyotp.TOTP(secret).now()})

    resp = client.post(
        "/admin/security/mfa/regenerate-recovery-codes",
        data={"code": "000000"},
        follow_redirects=False,
    )
    # Wrong code — still on the old set
    assert resp.status_code == 302
    admin = _get_user("admin@test.local")
    assert all(c["hash"] for c in admin.mfa_recovery_codes)

    resp = client.post(
        "/admin/security/mfa/regenerate-recovery-codes",
        data={"code": pyotp.TOTP(secret).now()},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/admin/security/mfa/recovery-codes" in resp.headers["Location"]
