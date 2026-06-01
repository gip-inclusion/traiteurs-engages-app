from __future__ import annotations

import datetime as _dt
import uuid

import bcrypt
import pytest
from sqlalchemy import func, select


def _create_throwaway_user(role, *, company_id=None, caterer_id=None):
    from database import session_factory
    from models import User, UserRole

    s = session_factory()
    try:
        email = f"throwaway-{uuid.uuid4().hex[:8]}@example.com"
        u = User(
            email=email,
            password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
            first_name="Throw",
            last_name="Away",
            role=role if isinstance(role, UserRole) else UserRole(role),
            company_id=company_id,
            caterer_id=caterer_id,
        )
        s.add(u)
        s.commit()
        return u.id, email
    finally:
        s.close()


def _create_throwaway_caterer_user():
    from database import session_factory
    from models import Caterer, CatererStructureType, User, UserRole

    s = session_factory()
    try:
        suffix = uuid.uuid4().hex[:6]
        c = Caterer(
            name=f"Throw-Cat-{suffix}",
            siret=f"88{uuid.uuid4().int % 10**12:012d}"[:14],
            structure_type=CatererStructureType.ESAT,
            invoice_prefix=f"T{suffix[:5]}",
            is_validated=False,
        )
        s.add(c)
        s.flush()
        u = User(
            email=f"throw-cat-{suffix}@example.com",
            password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
            first_name="Cat",
            last_name="Away",
            role=UserRole.caterer,
            caterer_id=c.id,
        )
        s.add(u)
        s.commit()
        return u.id, c.id
    finally:
        s.close()


def _create_client_admin_with_company_and_self_employee():
    from database import session_factory
    from models import Company, CompanyEmployee, User, UserRole

    s = session_factory()
    try:
        suffix = uuid.uuid4().hex[:6]
        company = Company(
            name=f"Throw-Co-{suffix}",
            siret=f"77{uuid.uuid4().int % 10**12:012d}"[:14],
        )
        s.add(company)
        s.flush()
        email = f"client-self-{suffix}@example.com"
        u = User(
            email=email,
            password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
            first_name="Self",
            last_name="Client",
            role=UserRole.client_admin,
            company_id=company.id,
        )
        s.add(u)
        s.flush()
        emp = CompanyEmployee(
            company_id=company.id,
            first_name="Self",
            last_name="Client",
            email=email,
            user_id=u.id,
        )
        s.add(emp)
        s.commit()
        return u.id, company.id, emp.id
    finally:
        s.close()


def _company_exists(company_id) -> bool:
    from database import session_factory
    from models import Company

    s = session_factory()
    try:
        return s.get(Company, company_id) is not None
    finally:
        s.close()


def _employee_exists(employee_id) -> bool:
    from database import session_factory
    from models import CompanyEmployee

    s = session_factory()
    try:
        return s.get(CompanyEmployee, employee_id) is not None
    finally:
        s.close()


def _cleanup_user(user_id):
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        s.execute(User.__table__.delete().where(User.id == user_id))
        s.commit()
    finally:
        s.close()


def _cleanup_caterer(caterer_id):
    from database import session_factory
    from models import Caterer

    s = session_factory()
    try:
        s.execute(Caterer.__table__.delete().where(Caterer.id == caterer_id))
        s.commit()
    finally:
        s.close()


def _cleanup_company(company_id):
    from database import session_factory
    from models import Company

    s = session_factory()
    try:
        s.execute(Company.__table__.delete().where(Company.id == company_id))
        s.commit()
    finally:
        s.close()


def _user_exists(user_id) -> bool:
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        return s.get(User, user_id) is not None
    finally:
        s.close()


def _get_user(user_id):
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        return s.get(User, user_id)
    finally:
        s.close()


def _random_siret() -> str:
    return f"99{uuid.uuid4().int % 10**12:012d}"[:14]


def _random_prefix() -> str:
    return f"T{uuid.uuid4().hex[:5]}".upper()


def test_super_admin_can_list_users(client, login):
    login("admin@test.local")
    r = client.get("/admin/users")
    assert r.status_code == 200, r.data


@pytest.mark.parametrize(
    "user_email",
    ["alice@test.local", "bob@test.local", "cook@test.local"],
)
def test_non_super_admin_cannot_list_users(client, login, user_email):
    login(user_email)
    r = client.get("/admin/users", follow_redirects=False)
    assert r.status_code in (302, 403)


def test_anonymous_is_bounced(client):
    r = client.get("/admin/users", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_list_search_by_email_substring(client, login):
    login("admin@test.local")
    r = client.get("/admin/users?q=alice")
    assert r.status_code == 200
    assert b"alice@test.local" in r.data
    assert b"bob@test.local" not in r.data


def test_list_renders_select_for_mutable_users_and_badge_for_super_admin(client, login):
    login("admin@test.local")
    r = client.get("/admin/users")
    assert r.status_code == 200
    assert b'data-action="role-select"' in r.data
    assert b'value="super_admin"' not in r.data


def test_delete_vierge_user_succeeds(client, login):
    from models import UserRole

    user_id, _ = _create_throwaway_user(UserRole.client_user)
    try:
        login("admin@test.local")
        r = client.post(f"/admin/users/{user_id}/delete")
        assert r.status_code == 302, r.data
        assert not _user_exists(user_id)
    finally:
        if _user_exists(user_id):
            _cleanup_user(user_id)


def test_delete_refuses_self(client, login):
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        admin = s.scalar(select(User).where(User.email == "admin@test.local"))
        admin_id = admin.id
    finally:
        s.close()
    login("admin@test.local")
    r = client.post(f"/admin/users/{admin_id}/delete")
    assert r.status_code == 302
    assert _user_exists(admin_id)


def test_delete_a_super_admin_when_another_remains(client, login):
    from models import UserRole

    other_admin_id, _ = _create_throwaway_user(UserRole.super_admin)
    try:
        login("admin@test.local")
        r = client.post(f"/admin/users/{other_admin_id}/delete")
        assert r.status_code == 302
        assert not _user_exists(other_admin_id)
    finally:
        if _user_exists(other_admin_id):
            _cleanup_user(other_admin_id)


def test_delete_refuses_user_with_business_history(client, login):
    from database import session_factory
    from models import (
        Company,
        MealType,
        QuoteRequest,
        UserRole,
    )

    s = session_factory()
    try:
        acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
        company_id = acme.id
    finally:
        s.close()

    user_id, _ = _create_throwaway_user(UserRole.client_user, company_id=company_id)
    qr_id = None
    try:
        s = session_factory()
        try:
            qr = QuoteRequest(
                company_id=company_id,
                user_id=user_id,
                meal_type=MealType.plateaux_repas,
                event_date=_dt.date.today() + _dt.timedelta(days=30),
                guest_count=10,
            )
            s.add(qr)
            s.commit()
            qr_id = qr.id
        finally:
            s.close()

        login("admin@test.local")
        r = client.post(f"/admin/users/{user_id}/delete")
        assert r.status_code == 302
        assert _user_exists(user_id)
    finally:
        s = session_factory()
        try:
            if qr_id:
                s.execute(
                    QuoteRequest.__table__.delete().where(QuoteRequest.id == qr_id)
                )
            s.commit()
        finally:
            s.close()
        if _user_exists(user_id):
            _cleanup_user(user_id)


def test_delete_audit_logged(client, login):
    from database import session_factory
    from models import AuditLog, UserRole

    user_id, _ = _create_throwaway_user(UserRole.client_user)
    try:
        login("admin@test.local")
        s = session_factory()
        try:
            before = (
                s.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "user.delete"
                    )
                )
                or 0
            )
        finally:
            s.close()

        r = client.post(f"/admin/users/{user_id}/delete")
        assert r.status_code == 302

        s = session_factory()
        try:
            after = (
                s.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "user.delete"
                    )
                )
                or 0
            )
        finally:
            s.close()
        assert after == before + 1
    finally:
        if _user_exists(user_id):
            _cleanup_user(user_id)


def test_can_delete_blocks_user_with_orders(monkeypatch):
    from database import session_factory
    from models import User
    from services import user_admin
    from services.user_admin import can_delete_user

    def fake_metrics(_db, _user):
        return {
            "quote_requests": 0,
            "messages_sent": 0,
            "messages_received": 0,
            "employees": 0,
            "reviews": 0,
            "orders": 1,
        }

    monkeypatch.setattr(user_admin, "_user_metrics", fake_metrics)
    s = session_factory()
    try:
        actor = s.scalar(select(User).where(User.email == "admin@test.local"))
        target = s.scalar(select(User).where(User.email == "alice@test.local"))
        msg = can_delete_user(s, target, actor=actor)
        assert msg is not None
        assert "historique" in msg.lower()
    finally:
        s.close()


def test_can_delete_last_super_admin_is_blocked():
    from database import session_factory
    from models import User
    from services.user_admin import can_delete_user

    s = session_factory()
    try:
        seeded_admin = s.scalar(select(User).where(User.email == "admin@test.local"))
        actor = s.scalar(select(User).where(User.email == "alice@test.local"))
        msg = can_delete_user(s, seeded_admin, actor=actor)
        assert msg is not None
        assert "super administrateur" in msg.lower()
    finally:
        s.close()


def test_role_change_client_admin_to_client_user_inline(client, login):
    from database import session_factory
    from models import Company, UserRole

    s = session_factory()
    try:
        acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
        company_id = acme.id
    finally:
        s.close()

    user_id, _ = _create_throwaway_user(UserRole.client_admin, company_id=company_id)
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/role-change",
            data={"role": "client_user"},
        )
        assert r.status_code == 302, r.data
        u = _get_user(user_id)
        assert u.role == UserRole.client_user
        assert u.company_id == company_id
    finally:
        _cleanup_user(user_id)


def test_role_change_client_to_caterer_creates_caterer(client, login):
    from database import session_factory
    from models import Caterer, UserRole

    user_id, _ = _create_throwaway_user(UserRole.client_user)
    siret = _random_siret()
    prefix = _random_prefix()
    caterer_id_to_cleanup = None
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/role-change",
            data={
                "role": "caterer",
                "caterer_name": "Traiteur Test",
                "caterer_siret": siret,
                "structure_type": "ESAT",
                "invoice_prefix": prefix,
            },
        )
        assert r.status_code == 302, r.data
        u = _get_user(user_id)
        assert u.role == UserRole.caterer
        assert u.caterer_id is not None
        assert u.company_id is None
        caterer_id_to_cleanup = u.caterer_id

        s = session_factory()
        try:
            c = s.get(Caterer, caterer_id_to_cleanup)
            assert c.name == "Traiteur Test"
            assert c.siret == siret
            assert c.is_validated is False
        finally:
            s.close()
    finally:
        _cleanup_user(user_id)
        if caterer_id_to_cleanup:
            _cleanup_caterer(caterer_id_to_cleanup)


def test_role_change_client_admin_with_self_employee_to_caterer(client, login):
    from models import UserRole

    user_id, company_id, employee_id = (
        _create_client_admin_with_company_and_self_employee()
    )
    caterer_id_to_cleanup = None
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/role-change",
            data={
                "role": "caterer",
                "caterer_name": "Marine Traiteur",
                "caterer_siret": _random_siret(),
                "structure_type": "ESAT",
                "invoice_prefix": _random_prefix(),
            },
        )
        assert r.status_code == 302, r.data
        u = _get_user(user_id)
        assert u.role == UserRole.caterer
        assert u.caterer_id is not None
        assert u.company_id is None
        caterer_id_to_cleanup = u.caterer_id
        assert not _company_exists(company_id)
        assert not _employee_exists(employee_id)
    finally:
        _cleanup_user(user_id)
        if caterer_id_to_cleanup:
            _cleanup_caterer(caterer_id_to_cleanup)


def test_delete_client_with_only_self_employee_succeeds(client, login):
    user_id, company_id, employee_id = (
        _create_client_admin_with_company_and_self_employee()
    )
    try:
        login("admin@test.local")
        r = client.post(f"/admin/users/{user_id}/delete")
        assert r.status_code == 302, r.data
        assert not _user_exists(user_id)
        assert not _company_exists(company_id)
        assert not _employee_exists(employee_id)
    finally:
        if _user_exists(user_id):
            _cleanup_user(user_id)


def test_role_change_to_caterer_requires_inputs(client, login):
    from models import UserRole

    user_id, _ = _create_throwaway_user(UserRole.client_user)
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/role-change",
            data={"role": "caterer"},
        )
        assert r.status_code == 302
        u = _get_user(user_id)
        assert u.role == UserRole.client_user
        assert u.caterer_id is None
    finally:
        _cleanup_user(user_id)


def test_role_change_to_caterer_rejects_bad_siret(client, login):
    from models import UserRole

    user_id, _ = _create_throwaway_user(UserRole.client_user)
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/role-change",
            data={
                "role": "caterer",
                "caterer_name": "X",
                "caterer_siret": "abc",
                "structure_type": "ESAT",
                "invoice_prefix": "TST",
            },
        )
        assert r.status_code == 302
        u = _get_user(user_id)
        assert u.role == UserRole.client_user
    finally:
        _cleanup_user(user_id)


def test_role_change_caterer_to_client_rejects_duplicate_siret(client, login):
    from models import UserRole

    user_id, caterer_id = _create_throwaway_caterer_user()
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/role-change",
            data={
                "role": "client_admin",
                "company_name": "Doublon",
                "company_siret": "12345678901234",
            },
        )
        assert r.status_code == 302
        u = _get_user(user_id)
        assert u.role == UserRole.caterer
        assert u.caterer_id == caterer_id
        assert u.company_id is None
    finally:
        _cleanup_user(user_id)
        _cleanup_caterer(caterer_id)


def test_role_change_caterer_to_client_admin_creates_company(client, login):
    from database import session_factory
    from models import Company, UserRole

    user_id, caterer_id = _create_throwaway_caterer_user()
    company_id_to_cleanup = None
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/role-change",
            data={
                "role": "client_admin",
                "company_name": "Nouvelle Entreprise",
                "company_siret": _random_siret(),
            },
        )
        assert r.status_code == 302, r.data
        u = _get_user(user_id)
        assert u.role == UserRole.client_admin
        assert u.caterer_id is None
        assert u.company_id is not None
        company_id_to_cleanup = u.company_id

        s = session_factory()
        try:
            c = s.get(Company, company_id_to_cleanup)
            assert c.name == "Nouvelle Entreprise"
        finally:
            s.close()
    finally:
        _cleanup_user(user_id)
        if company_id_to_cleanup:
            _cleanup_company(company_id_to_cleanup)
        _cleanup_caterer(caterer_id)


def test_role_change_refuses_self(client, login):
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        admin = s.scalar(select(User).where(User.email == "admin@test.local"))
        admin_id = admin.id
    finally:
        s.close()
    login("admin@test.local")
    r = client.post(
        f"/admin/users/{admin_id}/role-change",
        data={"role": "client_user"},
    )
    assert r.status_code == 302
    u = _get_user(admin_id)
    from models import UserRole

    assert u.role == UserRole.super_admin


def test_role_change_refuses_super_admin_target(client, login):
    from models import UserRole

    other_admin_id, _ = _create_throwaway_user(UserRole.super_admin)
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{other_admin_id}/role-change",
            data={"role": "client_user"},
        )
        assert r.status_code == 302
        u = _get_user(other_admin_id)
        assert u.role == UserRole.super_admin
    finally:
        if _user_exists(other_admin_id):
            _cleanup_user(other_admin_id)


def test_role_change_refuses_to_super_admin(client, login):
    from models import UserRole

    user_id, _ = _create_throwaway_user(UserRole.client_user)
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/role-change",
            data={"role": "super_admin"},
        )
        assert r.status_code == 302
        u = _get_user(user_id)
        assert u.role == UserRole.client_user
    finally:
        _cleanup_user(user_id)


def test_role_change_refuses_business_history_on_nature_change(client, login):
    from database import session_factory
    from models import (
        Company,
        MealType,
        QuoteRequest,
        UserRole,
    )

    s = session_factory()
    try:
        acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
        company_id = acme.id
    finally:
        s.close()

    user_id, _ = _create_throwaway_user(UserRole.client_user, company_id=company_id)
    qr_id = None
    try:
        s = session_factory()
        try:
            qr = QuoteRequest(
                company_id=company_id,
                user_id=user_id,
                meal_type=MealType.plateaux_repas,
                event_date=_dt.date.today() + _dt.timedelta(days=30),
                guest_count=10,
            )
            s.add(qr)
            s.commit()
            qr_id = qr.id
        finally:
            s.close()

        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/role-change",
            data={
                "role": "caterer",
                "caterer_name": "X",
                "caterer_siret": _random_siret(),
                "structure_type": "ESAT",
                "invoice_prefix": _random_prefix(),
            },
        )
        assert r.status_code == 302
        u = _get_user(user_id)
        assert u.role == UserRole.client_user
    finally:
        s = session_factory()
        try:
            if qr_id:
                s.execute(
                    QuoteRequest.__table__.delete().where(QuoteRequest.id == qr_id)
                )
            s.commit()
        finally:
            s.close()
        if _user_exists(user_id):
            _cleanup_user(user_id)


def test_role_change_audit_logged(client, login):
    from database import session_factory
    from models import AuditLog, Company, UserRole

    s = session_factory()
    try:
        acme = s.scalar(select(Company).where(Company.siret == "12345678901234"))
        company_id = acme.id
    finally:
        s.close()

    user_id, _ = _create_throwaway_user(UserRole.client_admin, company_id=company_id)
    try:
        login("admin@test.local")
        s = session_factory()
        try:
            before = (
                s.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "user.role_change"
                    )
                )
                or 0
            )
        finally:
            s.close()

        r = client.post(
            f"/admin/users/{user_id}/role-change",
            data={"role": "client_user"},
        )
        assert r.status_code == 302

        s = session_factory()
        try:
            after = (
                s.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "user.role_change"
                    )
                )
                or 0
            )
        finally:
            s.close()
        assert after == before + 1
    finally:
        _cleanup_user(user_id)
