"""Tests pour `/admin/users` — gestion des comptes côté super_admin.

Trois flux à couvrir :

* **Liste & détail** : routes accessibles seulement au super_admin,
  filtres `?q=` / `?role=` fonctionnent, page 200 sur un user
  arbitraire.

* **Suppression** : autorisée sur un compte vierge (pas de QR /
  messages / employé / avis), refusée sur soi-même, sur le dernier
  super_admin, et sur un compte avec historique métier.

* **Conversion `client_*` → `caterer`** : crée la fiche traiteur,
  détache la Company (supprime si orpheline), bascule `role` +
  `caterer_id`. Refusée sur un super_admin / sur soi-même / si
  historique métier.

Stratégie d'isolation : on crée des users dédiés à chaque test (suffix
UUID dans l'email) pour ne pas casser les autres tests qui dépendent
des 4 users seedés par `conftest._seed_users`.
"""

from __future__ import annotations

import datetime as _dt
import uuid

import bcrypt
import pytest
from sqlalchemy import func, select


def _create_throwaway_user(role, *, company_id=None, caterer_id=None):
    """Crée un user dédié à un test, retourne son id + email."""
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


# ---------------------------------------------------------------------------
# Role gate
# ---------------------------------------------------------------------------


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
    assert r.status_code in (302, 403), (
        f"{user_email} must not reach /admin/users; got {r.status_code}"
    )


def test_anonymous_is_bounced(client):
    r = client.get("/admin/users", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


# ---------------------------------------------------------------------------
# Liste — recherche et filtre rôle
# ---------------------------------------------------------------------------


def test_list_search_by_email_substring(client, login):
    login("admin@test.local")
    r = client.get("/admin/users?q=alice")
    assert r.status_code == 200
    assert b"alice@test.local" in r.data
    # bob ne doit pas matcher "alice"
    assert b"bob@test.local" not in r.data


def test_list_filter_by_role_caterer(client, login):
    login("admin@test.local")
    r = client.get("/admin/users?role=caterer")
    assert r.status_code == 200
    assert b"cook@test.local" in r.data
    assert b"alice@test.local" not in r.data


def test_list_unknown_role_falls_back_to_all(client, login):
    """Un `?role=` tampered ne doit pas 500 — fallback silencieux sur
    "all"."""
    login("admin@test.local")
    r = client.get("/admin/users?role=hacker")
    assert r.status_code == 200
    assert b"alice@test.local" in r.data


# ---------------------------------------------------------------------------
# Détail
# ---------------------------------------------------------------------------


def test_detail_renders_for_an_existing_user(client, login):
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        alice = s.scalar(select(User).where(User.email == "alice@test.local"))
        alice_id = alice.id
    finally:
        s.close()
    login("admin@test.local")
    r = client.get(f"/admin/users/{alice_id}")
    assert r.status_code == 200
    assert b"alice@test.local" in r.data


def test_detail_404_for_unknown_user(client, login):
    login("admin@test.local")
    r = client.get(f"/admin/users/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------


def test_delete_vierge_user_succeeds(client, login):
    """Un user sans historique métier doit pouvoir être supprimé."""
    from models import UserRole

    user_id, _ = _create_throwaway_user(UserRole.client_user)
    try:
        login("admin@test.local")
        r = client.post(f"/admin/users/{user_id}/delete")
        assert r.status_code == 302, r.data
        assert not _user_exists(user_id)
    finally:
        # Idempotent cleanup if the test failed before delete
        if _user_exists(user_id):
            from database import session_factory
            from models import User

            s = session_factory()
            try:
                s.execute(User.__table__.delete().where(User.id == user_id))
                s.commit()
            finally:
                s.close()


def test_delete_refuses_self(client, login):
    """Le super_admin ne peut pas se supprimer lui-même."""
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
    # Doit renvoyer un redirect vers la page détail avec un flash erreur
    assert r.status_code == 302
    assert _user_exists(admin_id), "admin must still exist after self-delete attempt"


def test_delete_a_super_admin_when_another_remains(client, login):
    """Un super_admin peut être supprimé tant qu'il en reste un autre.
    La branche "dernier super_admin" elle-même est couverte unitairement
    par `test_can_delete_last_super_admin_is_blocked` ci-dessous."""
    from models import UserRole

    other_admin_id, _ = _create_throwaway_user(UserRole.super_admin)
    try:
        login("admin@test.local")
        r = client.post(f"/admin/users/{other_admin_id}/delete")
        assert r.status_code == 302, r.data
        assert not _user_exists(other_admin_id), (
            "un super_admin doit pouvoir etre supprime s'il en reste un autre"
        )
    finally:
        if _user_exists(other_admin_id):
            from database import session_factory
            from models import User

            s = session_factory()
            try:
                s.execute(User.__table__.delete().where(User.id == other_admin_id))
                s.commit()
            finally:
                s.close()


def test_delete_refuses_user_with_business_history(client, login):
    """Un user avec une QuoteRequest doit voir sa suppression refusée."""
    from database import session_factory
    from models import (
        Company,
        MealType,
        QuoteRequest,
        User,
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
        assert _user_exists(user_id), (
            "un user avec QR ne doit pas pouvoir etre supprime"
        )
    finally:
        s = session_factory()
        try:
            if qr_id:
                s.execute(
                    QuoteRequest.__table__.delete().where(QuoteRequest.id == qr_id)
                )
            s.execute(User.__table__.delete().where(User.id == user_id))
            s.commit()
        finally:
            s.close()


def test_delete_audit_logged(client, login):
    """L'action `user.delete` doit créer un AuditLog row côté admin."""
    from database import session_factory
    from models import AuditLog, UserRole

    user_id, email = _create_throwaway_user(UserRole.client_user)
    try:
        login("admin@test.local")
        before = 0
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
        from models import User

        s = session_factory()
        try:
            s.execute(User.__table__.delete().where(User.id == user_id))
            s.commit()
        finally:
            s.close()


# ---------------------------------------------------------------------------
# Tests unitaires du service (couvre les branches difficiles à exercer
# via la route HTTP — notamment "dernier super_admin")
# ---------------------------------------------------------------------------


def test_can_delete_last_super_admin_is_blocked():
    """Si supprimer `user` retirerait le dernier super_admin de la base,
    `can_delete_user` doit renvoyer un message bloquant — la route ne
    permet pas d'exercer ce cas avec la fixture seedée puisqu'on ne
    peut pas être à la fois l'auteur et la cible (self-block prioritaire)."""
    from database import session_factory
    from models import User
    from services.user_admin import can_delete_user

    s = session_factory()
    try:
        seeded_admin = s.scalar(select(User).where(User.email == "admin@test.local"))
        # Cible = seeded admin. Acteur = un user distinct quelconque
        # (le check `last_super_admin` se déclenche avant tout autre
        # garde-fou qui dépendrait de l'acteur).
        actor = s.scalar(select(User).where(User.email == "alice@test.local"))
        msg = can_delete_user(s, seeded_admin, actor=actor)
        assert msg is not None
        assert "super administrateur" in msg.lower()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Conversion client → traiteur
# ---------------------------------------------------------------------------


def test_convert_client_to_caterer_succeeds(client, login):
    """Un client_user vierge doit être convertible en caterer : son
    rôle bascule, `caterer_id` pointe sur une nouvelle fiche, la
    Company est détachée."""
    from database import session_factory
    from models import Caterer, User, UserRole

    user_id, _ = _create_throwaway_user(UserRole.client_user)
    # Suffixe random pour éviter les collisions sur SIRET et invoice_prefix
    # (UNIQUE) si un test précédent a laissé des rows.
    suffix = uuid.uuid4().hex[:4]
    test_siret = f"99{uuid.uuid4().int % 10**12:012d}"[:14]
    test_prefix = f"T{suffix[:5]}"
    caterer_id_to_cleanup = None
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/convert-to-caterer",
            data={
                "caterer_name": "Traiteur Test",
                "caterer_siret": test_siret,
                "structure_type": "ESAT",
                "invoice_prefix": test_prefix,
            },
        )
        assert r.status_code == 302, r.data

        u = _get_user(user_id)
        assert u.role == UserRole.caterer
        assert u.caterer_id is not None
        assert u.company_id is None
        caterer_id_to_cleanup = u.caterer_id

        # Vérifie la fiche caterer minimale
        s = session_factory()
        try:
            c = s.get(Caterer, caterer_id_to_cleanup)
            assert c.name == "Traiteur Test"
            assert c.siret == test_siret
            assert c.is_validated is False
        finally:
            s.close()
    finally:
        s = session_factory()
        try:
            s.execute(User.__table__.delete().where(User.id == user_id))
            if caterer_id_to_cleanup:
                s.execute(
                    Caterer.__table__.delete().where(
                        Caterer.id == caterer_id_to_cleanup
                    )
                )
            s.commit()
        finally:
            s.close()


def test_convert_refuses_caterer_role(client, login):
    """Un user qui est déjà caterer ne peut pas être 'converti'."""
    from models import UserRole

    user_id, _ = _create_throwaway_user(UserRole.caterer)
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/convert-to-caterer",
            data={
                "caterer_name": "X",
                "caterer_siret": "11111111111111",
                "structure_type": "ESAT",
                "invoice_prefix": "TST",
            },
        )
        assert r.status_code == 302
        u = _get_user(user_id)
        assert u.role == UserRole.caterer
        assert u.caterer_id is None  # pas de nouvelle fiche créée
    finally:
        from database import session_factory
        from models import User

        s = session_factory()
        try:
            s.execute(User.__table__.delete().where(User.id == user_id))
            s.commit()
        finally:
            s.close()


def test_convert_refuses_bad_siret(client, login):
    """SIRET non-14-chiffres doit être rejeté."""
    from models import UserRole

    user_id, _ = _create_throwaway_user(UserRole.client_user)
    try:
        login("admin@test.local")
        r = client.post(
            f"/admin/users/{user_id}/convert-to-caterer",
            data={
                "caterer_name": "X",
                "caterer_siret": "abc",
                "structure_type": "ESAT",
                "invoice_prefix": "TST",
            },
        )
        assert r.status_code == 302
        u = _get_user(user_id)
        assert u.role == UserRole.client_user  # pas de bascule
    finally:
        from database import session_factory
        from models import User

        s = session_factory()
        try:
            s.execute(User.__table__.delete().where(User.id == user_id))
            s.commit()
        finally:
            s.close()
