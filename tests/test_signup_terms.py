def _wipe_signup_users():
    from sqlalchemy import select

    from database import session_factory
    from models import CompanyEmployee, User

    s = session_factory()
    try:
        user_ids = list(
            s.scalars(select(User.id).where(User.email.like("terms-%@test.local")))
        )
        if user_ids:
            s.execute(
                CompanyEmployee.__table__.delete().where(
                    CompanyEmployee.user_id.in_(user_ids)
                )
            )
        s.execute(User.__table__.delete().where(User.email.like("terms-%@test.local")))
        s.commit()
    finally:
        s.close()


def _wipe_signup_companies(siret_prefix: str = "9999"):
    from sqlalchemy import select

    from database import session_factory
    from models import Company, CompanyEmployee, CompanyService

    s = session_factory()
    try:
        company_ids = list(
            s.scalars(select(Company.id).where(Company.siret.startswith(siret_prefix)))
        )
        if company_ids:
            s.execute(
                CompanyService.__table__.delete().where(
                    CompanyService.company_id.in_(company_ids)
                )
            )
            s.execute(
                CompanyEmployee.__table__.delete().where(
                    CompanyEmployee.company_id.in_(company_ids)
                )
            )
        s.execute(
            Company.__table__.delete().where(Company.siret.startswith(siret_prefix))
        )
        s.commit()
    finally:
        s.close()


def _seed_extra_terms_version(slug: str, effective_at):
    from database import session_factory
    from models import TermsVersion

    s = session_factory()
    try:
        row = TermsVersion(
            slug=slug,
            title=f"CGS {slug}",
            template_name=f"legal/cgs_{slug}.html",
            effective_at=effective_at,
        )
        s.add(row)
        s.commit()
        return row.id
    finally:
        s.close()


def _drop_terms_version(row_id):
    from database import session_factory
    from models import TermsVersion

    s = session_factory()
    try:
        s.execute(TermsVersion.__table__.delete().where(TermsVersion.id == row_id))
        s.commit()
    finally:
        s.close()


def _fetch_user(email):
    from sqlalchemy import select

    from database import session_factory
    from models import User

    s = session_factory()
    try:
        return s.scalar(select(User).where(User.email == email))
    finally:
        s.close()


def _active_terms_id():
    from database import session_factory
    from services.terms import current_terms_version

    s = session_factory()
    try:
        return current_terms_version(s).id
    finally:
        s.close()


def test_signup_client_admin_refuses_without_accept_terms(client):
    try:
        r = client.post(
            "/signup",
            data={
                "role": "client_admin",
                "email": "terms-newadmin@test.local",
                "password": "VeryStrongPw1!",
                "first_name": "Term",
                "last_name": "Refuse",
                "siret": "99990000000001",
            },
            follow_redirects=False,
        )
        assert r.status_code == 200, (
            f"signup without accept_terms must re-render, not redirect; got "
            f"{r.status_code}"
        )
        assert _fetch_user("terms-newadmin@test.local") is None, (
            "no User row may be created when accept_terms is missing"
        )
    finally:
        _wipe_signup_users()
        _wipe_signup_companies()


def test_signup_caterer_refuses_without_accept_terms(client):
    try:
        r = client.post(
            "/signup",
            data={
                "role": "caterer",
                "email": "terms-cook@test.local",
                "password": "VeryStrongPw1!",
                "first_name": "Term",
                "last_name": "Cook",
                "siret": "99990000000002",
            },
            follow_redirects=False,
        )
        assert r.status_code == 200, r.data
        assert _fetch_user("terms-cook@test.local") is None
    finally:
        _wipe_signup_users()
        _wipe_signup_companies()


def test_signup_invite_refuses_without_accept_terms(client):
    import datetime as _dt

    from sqlalchemy import select

    from database import session_factory
    from models import CompanyEmployee

    import hashlib as _hashlib

    token = "terms-refusal-token-eeeeeeeeeeeeeeeeeeeeeeeeeeee"
    token_digest = _hashlib.sha256(token.encode("utf-8")).hexdigest()
    s = session_factory()
    try:
        from models import Company

        acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
        emp = CompanyEmployee(
            company_id=acme.id,
            email="terms-invitee@test.local",
            first_name="Term",
            last_name="Invite",
            invite_token=token_digest,
            invited_at=_dt.datetime.utcnow(),
        )
        s.add(emp)
        s.commit()
        emp_id = emp.id
    finally:
        s.close()

    try:
        r = client.post(
            f"/signup/invite/{token}",
            data={"password": "VeryStrongPw1!"},
            follow_redirects=False,
        )
        assert r.status_code == 200, (
            f"invite redemption without accept_terms must re-render; got "
            f"{r.status_code}"
        )
        assert _fetch_user("terms-invitee@test.local") is None
    finally:
        _wipe_signup_users()
        s = session_factory()
        try:
            s.execute(
                CompanyEmployee.__table__.delete().where(CompanyEmployee.id == emp_id)
            )
            s.commit()
        finally:
            s.close()


def test_signup_client_admin_stamps_terms_version_and_timestamp(client):
    active_id = _active_terms_id()
    try:
        r = client.post(
            "/signup",
            data={
                "role": "client_admin",
                "email": "terms-stamped@test.local",
                "password": "VeryStrongPw1!",
                "first_name": "Term",
                "last_name": "Stamped",
                "siret": "99990000000003",
                "accept_terms": "on",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302, r.data

        u = _fetch_user("terms-stamped@test.local")
        assert u is not None, "successful signup must create the User"
        assert u.terms_accepted_version_id == active_id, (
            "stamp must match whichever version was in force at submit time"
        )
        assert u.terms_accepted_at is not None, (
            "terms_accepted_at must carry the wall-clock acceptance moment"
        )
    finally:
        _wipe_signup_users()
        _wipe_signup_companies()


def test_signup_pending_client_user_stamps_terms_too(client):
    active_id = _active_terms_id()
    try:
        r = client.post(
            "/signup",
            data={
                "role": "client_admin",
                "email": "terms-pending@test.local",
                "password": "VeryStrongPw1!",
                "first_name": "Term",
                "last_name": "Pending",
                "siret": "12345678901234",
                "accept_terms": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302, r.data

        u = _fetch_user("terms-pending@test.local")
        assert u is not None
        assert u.terms_accepted_version_id == active_id
        assert u.terms_accepted_at is not None
    finally:
        _wipe_signup_users()


def test_current_terms_version_picks_the_latest_effective_row(app):
    import datetime as _dt

    from database import session_factory
    from services.terms import current_terms_version

    past_id = _seed_extra_terms_version("vpast", _dt.date(2020, 1, 1))
    future_id = _seed_extra_terms_version(
        "vfuture", _dt.date.today() + _dt.timedelta(days=365)
    )
    try:
        s = session_factory()
        try:
            today_active = current_terms_version(s, today=_dt.date.today())
            assert today_active.id != future_id, (
                "future version must not be 'in force' on today"
            )

            past_active = current_terms_version(s, today=_dt.date(2020, 6, 1))
            assert past_active.id == past_id, (
                "the date-tie resolver must pick the past row when today < v1"
            )

            far_future = current_terms_version(
                s, today=_dt.date.today() + _dt.timedelta(days=400)
            )
            assert far_future.id == future_id, (
                "once a future version's effective_at is reached it must win"
            )
        finally:
            s.close()
    finally:
        _drop_terms_version(past_id)
        _drop_terms_version(future_id)
