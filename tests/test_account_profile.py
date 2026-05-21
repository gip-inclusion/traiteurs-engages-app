"""Tests pour le changement d'adresse e-mail depuis « Mon profil ».

Le helper partagé `services.account.apply_profile_form` est appelé par
trois routes :

    * `client.profile`       (`/client/profile`, POST)   — client_admin + client_user
    * `caterer.account_save` (`/caterer/account`, POST)  — caterer
    * `admin.profile`        (`/admin/profile`, POST)    — super_admin

Le contrat à valider est identique pour les trois :

    * un POST avec le même e-mail (ou vide) → succès, prénom/nom mis à
      jour, mot de passe non requis ;
    * un POST qui change l'e-mail SANS mot de passe → 400, e-mail intact ;
    * un POST avec un mot de passe incorrect → 400, e-mail intact ;
    * un POST avec un e-mail déjà pris → 400, e-mail intact ;
    * un POST valide (mot de passe correct + e-mail libre) → 302,
      e-mail effectivement modifié.

Le mot de passe seedé est `testpass`. Chaque test mutant restaure l'état
dans un `finally` pour ne pas casser les autres.
"""

import pytest
from sqlalchemy import select


# Mapping rôle → (email seedé, URL POST, URL GET de redirection attendue)
_ROUTES = {
    "client_admin": ("alice@test.local", "/client/profile", "/client/profile"),
    "client_user": ("bob@test.local", "/client/profile", "/client/profile"),
    "caterer": ("cook@test.local", "/caterer/account", "/caterer/account"),
    "super_admin": ("admin@test.local", "/admin/profile", "/admin/profile"),
}


def _get_user(email):
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        return s.scalar(select(User).where(User.email == email))
    finally:
        s.close()


def _reset_user(original_email, current_email, first_name, last_name):
    """Restore the user's email + names after a mutating test."""
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        u = s.scalar(select(User).where(User.email == current_email))
        if u is None:
            return
        u.email = original_email
        u.first_name = first_name
        u.last_name = last_name
        s.commit()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Happy path — name update only (no email change, no password required)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(_ROUTES.keys()))
def test_name_only_update_works_for_every_role(client, login, role):
    email, post_url, _ = _ROUTES[role]
    original = _get_user(email)
    original_first, original_last = original.first_name, original.last_name
    try:
        login(email)
        r = client.post(
            post_url,
            data={
                "first_name": "Nouveau",
                "last_name": "Nom",
                "email": email,  # unchanged
                "current_password": "",
            },
        )
        assert r.status_code == 302, r.data
        u = _get_user(email)
        assert u.first_name == "Nouveau"
        assert u.last_name == "Nom"
        assert u.email == email
    finally:
        _reset_user(email, email, original_first, original_last)


# ---------------------------------------------------------------------------
# Happy path — email change with valid current password
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(_ROUTES.keys()))
def test_email_change_with_valid_password_works(client, login, role):
    email, post_url, _ = _ROUTES[role]
    original = _get_user(email)
    original_first, original_last = original.first_name, original.last_name
    new_email = f"changed-{role}@example.com"
    try:
        login(email)
        r = client.post(
            post_url,
            data={
                "first_name": original_first,
                "last_name": original_last,
                "email": new_email,
                "current_password": "testpass",
            },
        )
        assert r.status_code == 302, r.data
        u = _get_user(new_email)
        assert u is not None, "the new email must exist in the DB"
        assert u.email == new_email
        # Old email is gone.
        assert _get_user(email) is None
    finally:
        _reset_user(email, new_email, original_first, original_last)


# ---------------------------------------------------------------------------
# Rejections — wrong password, missing password, collision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(_ROUTES.keys()))
def test_email_change_without_password_is_rejected(client, login, role):
    email, post_url, _ = _ROUTES[role]
    original = _get_user(email)
    original_first, original_last = original.first_name, original.last_name
    try:
        login(email)
        r = client.post(
            post_url,
            data={
                "first_name": original_first,
                "last_name": original_last,
                "email": f"sneaky-{role}@example.com",
                "current_password": "",  # missing
            },
        )
        assert r.status_code == 400, r.data
        # Email untouched.
        assert _get_user(email) is not None
        assert _get_user(f"sneaky-{role}@example.com") is None
    finally:
        _reset_user(email, email, original_first, original_last)


@pytest.mark.parametrize("role", list(_ROUTES.keys()))
def test_email_change_wrong_password_is_rejected(client, login, role):
    email, post_url, _ = _ROUTES[role]
    original = _get_user(email)
    original_first, original_last = original.first_name, original.last_name
    try:
        login(email)
        r = client.post(
            post_url,
            data={
                "first_name": original_first,
                "last_name": original_last,
                "email": f"sneaky-{role}@example.com",
                "current_password": "not-the-right-one",
            },
        )
        assert r.status_code == 400, r.data
        assert _get_user(email) is not None
        assert _get_user(f"sneaky-{role}@example.com") is None
    finally:
        _reset_user(email, email, original_first, original_last)


@pytest.mark.parametrize("role", list(_ROUTES.keys()))
def test_email_change_collision_is_rejected(client, login, role):
    """If the target email is already used by another account, the save
    must abort cleanly with a 400, not raise a 500 on IntegrityError."""
    email, post_url, _ = _ROUTES[role]
    # Pick a different seeded email to collide with — any of the other
    # three seeded accounts works.
    other_email = next(e for e, _, _ in _ROUTES.values() if e != email)
    original = _get_user(email)
    original_first, original_last = original.first_name, original.last_name
    try:
        login(email)
        r = client.post(
            post_url,
            data={
                "first_name": original_first,
                "last_name": original_last,
                "email": other_email,
                "current_password": "testpass",
            },
        )
        assert r.status_code == 400, r.data
        # Original email still owns the row.
        u = _get_user(email)
        assert u is not None
        assert u.email == email
    finally:
        _reset_user(email, email, original_first, original_last)
