import datetime as _dt
import hashlib as _hashlib
import re as _re

import bcrypt
from sqlalchemy import select


def _digest(raw: str) -> str:
    return _hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _acme_id():
    from database import session_factory
    from models import Company

    s = session_factory()
    try:
        return s.scalar(select(Company).where(Company.siret == "12345678901234")).id
    finally:
        s.close()


def _alice_id():
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        return s.scalar(select(User).where(User.email == "alice@test.local")).id
    finally:
        s.close()


def _bob_id():
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        return s.scalar(select(User).where(User.email == "bob@test.local")).id
    finally:
        s.close()


def _create_throwaway_acme_user(*, email_prefix="throwaway", status=None):
    import uuid as _uuid

    import bcrypt as _bcrypt

    from database import session_factory
    from models import MembershipStatus, User, UserRole

    s = session_factory()
    try:
        suffix = _uuid.uuid4().hex[:8]
        u = User(
            email=f"{email_prefix}-{suffix}@example.com",
            password_hash=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
            first_name="Throw",
            last_name="Away",
            role=UserRole.client_user,
            company_id=_acme_id(),
            membership_status=status or MembershipStatus.active,
        )
        s.add(u)
        s.commit()
        return u.id
    finally:
        s.close()


def _fetch_user(user_id):
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        return s.get(User, user_id)
    finally:
        s.close()


def _cleanup_user(user_id):
    from database import session_factory
    from models import PasswordResetToken, User

    s = session_factory()
    try:
        s.execute(
            PasswordResetToken.__table__.delete().where(
                PasswordResetToken.user_id == user_id
            )
        )
        s.execute(User.__table__.delete().where(User.id == user_id))
        s.commit()
    finally:
        s.close()


def _ensure_employee(email, *, user_id=None, invite_token=None, invited_at=None):
    from database import session_factory
    from models import CompanyEmployee

    company_id = _acme_id()
    s = session_factory()
    try:
        row = s.scalar(
            select(CompanyEmployee).where(
                CompanyEmployee.company_id == company_id,
                CompanyEmployee.email == email,
            )
        )
        if row is None:
            row = CompanyEmployee(
                company_id=company_id,
                first_name="Test",
                last_name="Employee",
                email=email,
            )
            s.add(row)
        row.user_id = user_id
        row.invite_token = _digest(invite_token) if invite_token else None
        row.invited_at = invited_at
        s.commit()
        return row.id
    finally:
        s.close()


def _fetch_employee(employee_id):
    from database import session_factory
    from models import CompanyEmployee

    s = session_factory()
    try:
        return s.get(CompanyEmployee, employee_id)
    finally:
        s.close()


def test_admin_cannot_delete_own_effectifs_row(client, login):
    row_id = _ensure_employee("alice-self@test.local", user_id=_alice_id())

    login("alice@test.local")
    resp = client.post(
        f"/client/team/employees/{row_id}/delete", follow_redirects=False
    )
    assert resp.status_code == 302
    assert _fetch_employee(row_id) is not None, (
        "self-delete must not remove the admin's own effectifs row"
    )


def test_admin_can_delete_other_effectifs_row(client, login):
    victim_id = _create_throwaway_acme_user(email_prefix="delete-victim")
    try:
        row_id = _ensure_employee("colleague-to-delete@test.local", user_id=victim_id)

        login("alice@test.local")
        resp = client.post(
            f"/client/team/employees/{row_id}/delete", follow_redirects=False
        )
        assert resp.status_code == 302
        assert _fetch_employee(row_id) is None, "non-self deletes must still work"
    finally:
        _cleanup_user(victim_id)


def test_delete_linked_employee_revokes_target_session_and_detaches(client, login):
    from database import session_factory
    from models import PasswordResetToken, User
    from services import password_reset as pr

    victim_id = _create_throwaway_acme_user(email_prefix="detach-victim")
    try:
        s = session_factory()
        try:
            row, _raw = pr.issue_token(s, user=s.get(User, victim_id))
            s.commit()
            token_id = row.id
        finally:
            s.close()

        row_id = _ensure_employee("detach-target@test.local", user_id=victim_id)
        login("alice@test.local")
        resp = client.post(
            f"/client/team/employees/{row_id}/delete", follow_redirects=False
        )
        assert resp.status_code == 302

        from models import MembershipStatus

        victim = _fetch_user(victim_id)
        assert victim is not None, "the User must still exist (only detached)"
        assert victim.company_id is None, "company link must be cut"
        assert victim.membership_status == MembershipStatus.rejected
        assert victim.sessions_invalidated_at is not None, (
            "session cookies must be evicted via sessions_invalidated_at bump"
        )

        s = session_factory()
        try:
            refreshed = s.get(PasswordResetToken, token_id)
            assert refreshed.used_at is not None, (
                "outstanding reset token must be invalidated on detach"
            )
        finally:
            s.close()
    finally:
        _cleanup_user(victim_id)


def test_team_reject_revokes_target_session_and_tokens(client, login):
    from database import session_factory
    from models import MembershipStatus, PasswordResetToken, User
    from services import password_reset as pr

    pending_id = _create_throwaway_acme_user(
        email_prefix="reject-target", status=MembershipStatus.pending
    )
    try:
        s = session_factory()
        try:
            row, _raw = pr.issue_token(s, user=s.get(User, pending_id))
            s.commit()
            token_id = row.id
        finally:
            s.close()

        login("alice@test.local")
        resp = client.post(f"/client/team/reject/{pending_id}", follow_redirects=False)
        assert resp.status_code == 302

        target = _fetch_user(pending_id)
        assert target.membership_status == MembershipStatus.rejected
        assert target.sessions_invalidated_at is not None, (
            "rejection must bump sessions_invalidated_at to evict cookies"
        )

        s = session_factory()
        try:
            refreshed = s.get(PasswordResetToken, token_id)
            assert refreshed.used_at is not None, (
                "outstanding reset token must be invalidated on rejection"
            )
        finally:
            s.close()
    finally:
        _cleanup_user(pending_id)


def test_create_employee_generates_invite_token(client, login):
    login("alice@test.local")
    resp = client.post(
        "/client/team/employees",
        data={
            "first_name": "Newbie",
            "last_name": "Tester",
            "email": "newbie-create@example.com",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "invite=" in resp.headers["Location"], (
        "redirect must carry ?invite=<id> so the team page can pop the modal"
    )

    from database import session_factory
    from models import CompanyEmployee

    s = session_factory()
    try:
        row = s.scalar(
            select(CompanyEmployee).where(
                CompanyEmployee.email == "newbie-create@example.com"
            )
        )
        assert row is not None
        assert row.invite_token is not None
        assert len(row.invite_token) == 64, (
            f"expected 64-char SHA-256 hex, got len={len(row.invite_token)}"
        )
        assert _re.fullmatch(r"[0-9a-f]+", row.invite_token), (
            "stored token must be lowercase hex (SHA-256 digest)"
        )
        assert row.invited_at is not None
        assert row.user_id is None
    finally:
        s.close()


def test_invite_rotation_changes_token(client, login):
    employee_id = _ensure_employee(
        "rotate-target@test.local",
        invite_token="initial-token-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        invited_at=_dt.datetime.utcnow(),
    )
    before = _fetch_employee(employee_id).invite_token

    login("alice@test.local")
    resp = client.post(
        f"/client/team/employees/{employee_id}/invite", follow_redirects=False
    )
    assert resp.status_code == 302

    after = _fetch_employee(employee_id).invite_token
    assert after and after != before, "rotation must produce a fresh token"
    assert len(after) == 64, f"rotated digest wrong length: {len(after)}"
    assert _re.fullmatch(r"[0-9a-f]+", after), (
        "rotated digest must be lowercase hex (SHA-256)"
    )


def test_invite_revoke_clears_token(client, login):
    employee_id = _ensure_employee(
        "revoke-target@test.local",
        invite_token="some-live-token-yyyyyyyyyyyyyyyyyyyyyyyyyyy",
        invited_at=_dt.datetime.utcnow(),
    )

    login("alice@test.local")
    resp = client.post(
        f"/client/team/employees/{employee_id}/invite/revoke",
        follow_redirects=False,
    )
    assert resp.status_code == 302

    row = _fetch_employee(employee_id)
    assert row.invite_token is None
    assert row.invited_at is None


def test_invite_for_already_linked_employee_is_noop(client, login):
    employee_id = _ensure_employee("already-linked@test.local", user_id=_bob_id())

    login("alice@test.local")
    client.post(f"/client/team/employees/{employee_id}/invite", follow_redirects=False)
    row = _fetch_employee(employee_id)
    assert row.invite_token is None, "must not mint a token for a linked employee"
    assert row.invited_at is None, "must not stamp invited_at for a linked employee"


def test_signup_invite_get_renders_form_for_valid_token(client):
    token = "valid-redeem-token-aaaaaaaaaaaaaaaaaaaaaaaaaa"
    _ensure_employee(
        "redeem-form@test.local",
        invite_token=token,
        invited_at=_dt.datetime.utcnow(),
    )
    resp = client.get(f"/signup/invite/{token}")
    assert resp.status_code == 200
    assert b"redeem-form@test.local" in resp.data


def test_signup_invite_invalid_token_returns_404(client):
    resp = client.get("/signup/invite/this-token-does-not-exist")
    assert resp.status_code == 404


def test_signup_invite_expired_token_returns_404(client):
    token = "expired-token-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    _ensure_employee(
        "expired@test.local",
        invite_token=token,
        invited_at=_dt.datetime.utcnow() - _dt.timedelta(days=8),
    )
    resp = client.get(f"/signup/invite/{token}")
    assert resp.status_code == 404


def test_signup_invite_redemption_creates_user_and_consumes_token(client):
    token = "good-redeem-token-cccccccccccccccccccccccccc"
    employee_id = _ensure_employee(
        "redeem-success@test.local",
        invite_token=token,
        invited_at=_dt.datetime.utcnow(),
    )
    resp = client.post(
        f"/signup/invite/{token}",
        data={"password": "VeryStrongPassword1!", "accept_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, (
        f"successful redemption must redirect to dashboard; got {resp.status_code}"
    )

    from database import session_factory
    from models import MembershipStatus, User, UserRole

    s = session_factory()
    try:
        u = s.scalar(select(User).where(User.email == "redeem-success@test.local"))
        assert u is not None, "redemption must create the user"
        assert u.role == UserRole.client_user
        assert u.membership_status == MembershipStatus.active, (
            "invite-flow signup bypasses pending-approval"
        )
        assert u.company_id == _acme_id()
    finally:
        s.close()

    row = _fetch_employee(employee_id)
    assert row.user_id is not None
    assert row.invite_token is None, "token must be cleared on redemption"


def test_signup_invite_token_is_single_use(client):
    token = "single-use-token-ddddddddddddddddddddddddddd"
    _ensure_employee(
        "single-use@test.local",
        invite_token=token,
        invited_at=_dt.datetime.utcnow(),
    )

    first = client.post(
        f"/signup/invite/{token}",
        data={"password": "FirstUseStrong1!", "accept_terms": "on"},
        follow_redirects=False,
    )
    assert first.status_code == 302

    fresh = client.application.test_client()
    second = fresh.get(f"/signup/invite/{token}")
    assert second.status_code == 404


def test_signup_invite_ignores_tampered_email_in_post(client):
    token = "tamper-test-token-eeeeeeeeeeeeeeeeeeeeeeeeee"
    _ensure_employee(
        "real-email@test.local",
        invite_token=token,
        invited_at=_dt.datetime.utcnow(),
    )
    resp = client.post(
        f"/signup/invite/{token}",
        data={
            "password": "TamperResistant1!",
            "accept_terms": "on",
            "email": "evil@attacker.tld",
            "first_name": "Mallory",
            "last_name": "Hacker",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    from database import session_factory
    from models import User

    s = session_factory()
    try:
        good = s.scalar(select(User).where(User.email == "real-email@test.local"))
        evil = s.scalar(select(User).where(User.email == "evil@attacker.tld"))
        assert good is not None, "user must be created with the row's email"
        assert evil is None, "tampered email must be ignored, not honored"
    finally:
        s.close()


def test_signup_invite_weak_password_rejected(client):
    token = "weak-pw-token-ffffffffffffffffffffffffffffff"
    _ensure_employee(
        "weak-pw@test.local",
        invite_token=token,
        invited_at=_dt.datetime.utcnow(),
    )
    resp = client.post(
        f"/signup/invite/{token}",
        data={"password": "short", "accept_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 200

    from database import session_factory
    from models import User

    s = session_factory()
    try:
        u = s.scalar(select(User).where(User.email == "weak-pw@test.local"))
        assert u is None, "weak password must not create the user"
    finally:
        s.close()


def test_signup_invite_collision_with_existing_user_redirects_to_login(client):
    token = "collision-token-ggggggggggggggggggggggggggg"
    _ensure_employee(
        "preexisting@test.local",
        invite_token=token,
        invited_at=_dt.datetime.utcnow(),
    )
    from database import session_factory
    from models import MembershipStatus, User, UserRole

    s = session_factory()
    try:
        if s.scalar(select(User).where(User.email == "preexisting@test.local")) is None:
            s.add(
                User(
                    email="preexisting@test.local",
                    password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
                    first_name="Pre",
                    last_name="Existing",
                    role=UserRole.client_user,
                    company_id=_acme_id(),
                    membership_status=MembershipStatus.active,
                )
            )
            s.commit()
    finally:
        s.close()

    resp = client.post(
        f"/signup/invite/{token}",
        data={"password": "AnotherStrongOne1!", "accept_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_signup_invite_handles_integrity_error_at_flush(client, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    token = "race-token-iiiiiiiiiiiiiiiiiiiiiiiiiiiii"
    employee_id = _ensure_employee(
        "race-flush@example.com",
        invite_token=token,
        invited_at=_dt.datetime.utcnow(),
    )

    real_flush = Session.flush
    fired = {"n": 0}

    def patched_flush(self, *args, **kwargs):
        for obj in self.new:
            if getattr(obj, "email", None) == "race-flush@example.com":
                fired["n"] += 1
                raise IntegrityError(
                    "simulated race on users.email",
                    None,
                    Exception("duplicate key"),
                )
        return real_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", patched_flush)

    resp = client.post(
        f"/signup/invite/{token}",
        data={"password": "RaceResistantPwd1!", "accept_terms": "on"},
        follow_redirects=False,
    )

    assert fired["n"] >= 1, "simulated IntegrityError must have been triggered"
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"], (
        f"expected redirect to /login, got {resp.headers.get('Location')}"
    )

    row = _fetch_employee(employee_id)
    assert row.invite_token == _digest(token), (
        "rollback must preserve the invite digest"
    )
    assert row.user_id is None


def test_create_employee_rejects_duplicate_email_in_company(client, login):
    _ensure_employee("dup-target@example.com")

    login("alice@test.local")
    resp = client.post(
        "/client/team/employees",
        data={
            "first_name": "Duplicate",
            "last_name": "Attempt",
            "email": "dup-target@example.com",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    from database import session_factory
    from models import CompanyEmployee

    s = session_factory()
    try:
        rows = s.scalars(
            select(CompanyEmployee).where(
                CompanyEmployee.company_id == _acme_id(),
                CompanyEmployee.email == "dup-target@example.com",
            )
        ).all()
        assert len(rows) == 1, "duplicate email in same company must be rejected"
    finally:
        s.close()


def test_approve_clears_stale_invite_token_on_existing_row(client, login):
    pending_email = "approve-stale@test.local"

    employee_id = _ensure_employee(
        pending_email,
        invite_token="stale-token-hhhhhhhhhhhhhhhhhhhhhhhhhh",
        invited_at=_dt.datetime.utcnow(),
    )

    from database import session_factory
    from models import MembershipStatus, User, UserRole

    s = session_factory()
    try:
        if s.scalar(select(User).where(User.email == pending_email)) is None:
            s.add(
                User(
                    email=pending_email,
                    password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
                    first_name="Pending",
                    last_name="Member",
                    role=UserRole.client_user,
                    company_id=_acme_id(),
                    membership_status=MembershipStatus.pending,
                )
            )
            s.commit()
        pending_user_id = s.scalar(select(User.id).where(User.email == pending_email))
    finally:
        s.close()

    login("alice@test.local")
    resp = client.post(
        f"/client/team/approve/{pending_user_id}", follow_redirects=False
    )
    assert resp.status_code == 302

    row = _fetch_employee(employee_id)
    assert row.user_id == pending_user_id, "row must link to the approved user"
    assert row.invite_token is None, "stale invite token must be cleared"
    assert row.invited_at is None


def _seed_quote_request(user_id):
    from database import session_factory
    from models import QuoteRequest, QuoteRequestStatus

    s = session_factory()
    try:
        qr = QuoteRequest(
            company_id=_acme_id(),
            user_id=user_id,
            guest_count=10,
            status=QuoteRequestStatus.draft,
            event_address="1 rue Test",
            event_city="Paris",
            event_zip_code="75001",
            event_date=_dt.date.today() + _dt.timedelta(days=30),
        )
        s.add(qr)
        s.commit()
        return qr.id
    finally:
        s.close()


def test_client_user_sees_only_own_requests(client, login):
    alice_qr = _seed_quote_request(_alice_id())
    bob_qr = _seed_quote_request(_bob_id())

    login("bob@test.local")
    resp = client.get("/client/requests")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert str(bob_qr) in body, "bob must see his own QR"
    assert str(alice_qr) not in body, (
        "client_user must not see another company member's QR"
    )


def test_client_admin_sees_all_company_requests(client, login):
    alice_qr = _seed_quote_request(_alice_id())
    bob_qr = _seed_quote_request(_bob_id())

    login("alice@test.local")
    resp = client.get("/client/requests")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="replace")
    assert str(bob_qr) in body, "admin must see colleagues' QRs"
    assert str(alice_qr) in body, "admin must see her own QRs"


def test_client_user_cannot_load_other_users_request_detail(client, login):
    alice_qr = _seed_quote_request(_alice_id())

    login("bob@test.local")
    resp = client.get(f"/client/requests/{alice_qr}", follow_redirects=False)
    assert resp.status_code == 404, (
        f"client_user must 404 on a colleague's QR; got {resp.status_code}"
    )
