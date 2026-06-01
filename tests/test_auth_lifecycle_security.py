from __future__ import annotations

from sqlalchemy import select


def _make_super_admin(email: str, password: str = "OldPw!Old!Pw!1234"):
    import bcrypt
    from database import session_factory
    from models import User, UserRole

    s = session_factory()
    try:
        existing = s.scalar(select(User).where(User.email == email))
        if existing:
            s.delete(existing)
            s.commit()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
            first_name="Test",
            last_name="Admin",
            role=UserRole.super_admin,
            is_active=True,
        )
        s.add(user)
        s.commit()
        return user.id, user.sessions_invalidated_at
    finally:
        s.close()


def test_cli_reset_password_bumps_session_revocation_epoch(app):
    from cli import reset_password
    from database import session_factory
    from models import User

    email = "h5-reset@test.local"
    user_id, before = _make_super_admin(email)
    assert before is None, (
        "fixture left sessions_invalidated_at non-null — the test no "
        "longer covers the NULL→value transition we care about"
    )

    runner = app.test_cli_runner()
    new_pw = "ReplacedNow1234!"
    result = runner.invoke(reset_password, [email], input=f"{new_pw}\n{new_pw}\n")
    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"

    s = session_factory()
    try:
        u = s.get(User, user_id)
        assert u.sessions_invalidated_at is not None, (
            "reset-password did NOT bump sessions_invalidated_at — the "
            "regression the audit H-5 flagged is still present"
        )
    finally:
        s.close()


def test_cli_create_admin_stamps_session_revocation_epoch(app):
    from cli import create_admin
    from database import session_factory
    from models import User

    email = "h5-fresh@test.local"
    s = session_factory()
    try:
        existing = s.scalar(select(User).where(User.email == email))
        if existing:
            s.delete(existing)
            s.commit()
    finally:
        s.close()

    runner = app.test_cli_runner()
    pw = "FreshPolicyPw99!"
    result = runner.invoke(
        create_admin,
        ["--email", email, "--first-name", "H5", "--last-name", "Fresh"],
        input=f"{pw}\n{pw}\n",
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"

    s = session_factory()
    try:
        u = s.scalar(select(User).where(User.email == email))
        assert u is not None
        assert u.sessions_invalidated_at is not None, (
            "create_admin did NOT stamp sessions_invalidated_at — first "
            "reset would not invalidate sessions"
        )
    finally:
        s.close()


def test_logout_invalidates_replayed_cookie_server_side(app):
    import bcrypt as _bcrypt

    from database import session_factory
    from models import User

    client = app.test_client()

    try:
        r = client.post(
            "/login",
            data={"email": "alice@test.local", "password": "testpass"},
            follow_redirects=False,
        )
        assert r.status_code == 302, f"login should redirect, got {r.status_code}"
        captured = client.get_cookie("session")
        assert captured is not None, "login must Set-Cookie a session entry"
        captured_value = captured.value

        r = client.get("/client/dashboard", follow_redirects=False)
        assert r.status_code == 200, (
            "captured cookie must work BEFORE logout — otherwise the test "
            f"doesn't prove logout closes the window; got {r.status_code}"
        )

        r = client.post("/logout", follow_redirects=False)
        assert r.status_code == 302

        replay = app.test_client()
        replay.set_cookie("session", captured_value)
        r = replay.get("/client/dashboard", follow_redirects=False)
        assert r.status_code in (302, 403), (
            f"captured cookie kept working after legitimate user logged "
            f"out (got {r.status_code}); session revocation is not "
            f"enforced server-side"
        )

        import uuid as _uuid

        r = replay.post(
            "/api/messages",
            json={
                "recipient_id": str(_uuid.uuid4()),
                "body": "post-logout replay attempt",
            },
        )
        assert r.status_code in (302, 401, 403), (
            f"POST /api/messages still accepted on a post-logout replayed "
            f"cookie (got {r.status_code}); the messaging endpoint is "
            f"the specific symptom the field report described"
        )
    finally:
        s = session_factory()
        try:
            alice = s.scalar(select(User).where(User.email == "alice@test.local"))
            alice.sessions_invalidated_at = None
            alice.password_hash = _bcrypt.hashpw(
                b"testpass", _bcrypt.gensalt()
            ).decode()
            s.commit()
        finally:
            s.close()


def _seed_user_in_state(*, email: str, is_active: bool, membership):
    import bcrypt
    from sqlalchemy import select

    from database import session_factory
    from models import Company, User, UserRole

    password = "OracleTestPw!42"
    s = session_factory()
    try:
        company = s.scalar(select(Company).where(Company.siret == "12345678901234"))
        existing = s.scalar(select(User).where(User.email == email))
        if existing:
            s.delete(existing)
            s.commit()
        u = User(
            email=email,
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
            first_name="O",
            last_name="O",
            role=UserRole.client_user,
            company_id=company.id,
            is_active=is_active,
            membership_status=membership,
        )
        s.add(u)
        s.commit()
        return password
    finally:
        s.close()


def _wipe_oracle_users():
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        s.execute(User.__table__.delete().where(User.email.like("oracle-%@test.local")))
        s.commit()
    finally:
        s.close()


def _extract_flash_block(html: str) -> str:
    import re

    m = re.search(r'<div[^>]*role="alert"[^>]*>.*?</div>', html, flags=re.DOTALL)
    return m.group(0) if m else html


def test_login_flash_identical_for_all_inactive_states(client):
    from models import MembershipStatus

    cases = [
        ("oracle-disabled@test.local", False, MembershipStatus.active),
        ("oracle-pending@test.local", True, MembershipStatus.pending),
        ("oracle-rejected@test.local", True, MembershipStatus.rejected),
    ]

    try:
        flashes: list[str] = []
        for email, active, membership in cases:
            password = _seed_user_in_state(
                email=email, is_active=active, membership=membership
            )
            r = client.post(
                "/login",
                data={"email": email, "password": password},
                follow_redirects=False,
            )
            assert r.status_code == 200, (
                f"non-200 for {email}: got {r.status_code}, leaks state on its own"
            )
            flashes.append(
                _extract_flash_block(r.data.decode("utf-8", errors="replace"))
            )

        assert flashes[0] == flashes[1] == flashes[2], (
            "login flash MUST be identical across inactive states; got distinct "
            "payloads:\n - disabled:\n"
            + flashes[0]
            + "\n - pending:\n"
            + flashes[1]
            + "\n - rejected:\n"
            + flashes[2]
        )
    finally:
        _wipe_oracle_users()


def test_login_flash_does_not_leak_state_keywords(client):
    from models import MembershipStatus

    try:
        password = _seed_user_in_state(
            email="oracle-keyword@test.local",
            is_active=False,
            membership=MembershipStatus.active,
        )
        r = client.post(
            "/login",
            data={"email": "oracle-keyword@test.local", "password": password},
            follow_redirects=False,
        )
        body = r.data.decode("utf-8", errors="replace").lower()
        for forbidden in ("desactive", "rattachement", "refus", "en attente"):
            assert forbidden not in body, (
                f"flash leaks the '{forbidden}' keyword — H-2 oracle is back"
            )
    finally:
        _wipe_oracle_users()
