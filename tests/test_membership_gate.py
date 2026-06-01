def _seed_pending_user():
    import bcrypt
    from sqlalchemy import select

    from database import session_factory
    from models import Company, MembershipStatus, User, UserRole

    s = session_factory()
    try:
        acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
        email = "pending@test.local"
        existing = s.scalar(select(User).where(User.email == email))
        if existing is None:
            s.add(
                User(
                    email=email,
                    password_hash=bcrypt.hashpw(
                        b"pendingpw", bcrypt.gensalt()
                    ).decode(),
                    first_name="P",
                    last_name="P",
                    role=UserRole.client_user,
                    company_id=acme.id,
                    membership_status=MembershipStatus.pending,
                )
            )
            s.commit()
        return email, "pendingpw"
    finally:
        s.close()


def test_pending_user_login_refused(client):
    email, password = _seed_pending_user()

    resp = client.post(
        "/login", data={"email": email, "password": password}, follow_redirects=False
    )
    assert resp.status_code == 200, (
        f"login must NOT redirect a pending user to a dashboard; got {resp.status_code}"
    )


def test_pending_user_cannot_access_client_dashboard(client):
    email, password = _seed_pending_user()

    client.post("/login", data={"email": email, "password": password})

    resp = client.get("/client/dashboard", follow_redirects=False)
    assert resp.status_code in (302, 403), (
        f"pending member must be blocked; got {resp.status_code}"
    )


def test_pending_user_cannot_access_client_orders(client):
    email, password = _seed_pending_user()
    client.post("/login", data={"email": email, "password": password})

    resp = client.get("/client/orders", follow_redirects=False)
    assert resp.status_code in (302, 403), (
        f"pending member must be blocked from orders; got {resp.status_code}"
    )


def test_active_user_still_accesses_dashboard(client, login):
    login("alice@test.local")
    resp = client.get("/client/dashboard", follow_redirects=False)
    assert resp.status_code == 200, (
        f"active client_admin should still reach dashboard; got {resp.status_code}"
    )
