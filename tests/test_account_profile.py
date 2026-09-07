import pytest
from sqlalchemy import select


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
                "email": email,
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
        assert _get_user(email) is None
    finally:
        _reset_user(email, new_email, original_first, original_last)


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
                "current_password": "",
            },
        )
        assert r.status_code == 400, r.data
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
    email, post_url, _ = _ROUTES[role]
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
        u = _get_user(email)
        assert u is not None
        assert u.email == email
    finally:
        _reset_user(email, email, original_first, original_last)


def _set_phone(email, phone):
    from database import session_factory
    from models import User

    s = session_factory()
    try:
        u = s.scalar(select(User).where(User.email == email))
        u.phone = phone
        s.commit()
    finally:
        s.close()


def test_client_profile_saves_then_clears_phone(client, login):
    email = "alice@test.local"
    original = _get_user(email)
    base = {
        "first_name": original.first_name,
        "last_name": original.last_name,
        "email": email,
        "current_password": "",
    }
    try:
        login(email)
        r = client.post("/client/profile", data={**base, "phone": " 06 12 34 56 78 "})
        assert r.status_code == 302, r.data
        assert _get_user(email).phone == "06 12 34 56 78"

        r = client.post("/client/profile", data={**base, "phone": ""})
        assert r.status_code == 302, r.data
        assert _get_user(email).phone is None
    finally:
        _set_phone(email, None)


def test_profile_without_the_phone_field_keeps_it(client, login):
    # The caterer and admin profiles share apply_profile_form but never show
    # the field; saving there must not wipe a number set elsewhere.
    email = "cook@test.local"
    original = _get_user(email)
    try:
        _set_phone(email, "0601020304")
        login(email)
        r = client.post(
            "/caterer/account",
            data={
                "first_name": original.first_name,
                "last_name": original.last_name,
                "email": email,
                "current_password": "",
            },
        )
        assert r.status_code == 302, r.data
        assert _get_user(email).phone == "0601020304"
    finally:
        _set_phone(email, None)
