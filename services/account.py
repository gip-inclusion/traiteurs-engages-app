from __future__ import annotations

import bcrypt
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select

from models import User
from services.audit import log_admin_action
from services.password_reset import revoke_all_sessions


def apply_profile_form(db, user, form) -> str | None:
    first_name = (form.first_name.data or "").strip()
    last_name = (form.last_name.data or "").strip()
    new_email = (form.email.data or "").strip().lower()
    if not first_name or not last_name or not new_email:
        return "Prénom, nom et adresse e-mail sont obligatoires."

    user.first_name = first_name
    user.last_name = last_name

    if new_email == (user.email or "").lower():
        return None

    try:
        validate_email(new_email, check_deliverability=False)
    except EmailNotValidError:
        return "Adresse e-mail invalide."

    pwd = form.current_password.data or ""
    if not pwd or not bcrypt.checkpw(pwd.encode(), user.password_hash.encode()):
        return (
            "Mot de passe actuel incorrect. Le changement d'adresse "
            "e-mail nécessite une ré-authentification."
        )

    collision = db.scalar(
        select(User.id).where(User.email == new_email, User.id != user.id)
    )
    if collision:
        return "Cette adresse e-mail ne peut pas être utilisée pour ce compte."

    user.email = new_email
    revoke_all_sessions(db, user)
    log_admin_action(
        db,
        user,
        "account.email_change",
        target_type="user",
        target_id=user.id,
    )
    return None
