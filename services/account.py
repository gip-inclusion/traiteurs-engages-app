"""Mutations sur le profil personnel — partagées entre tous les rôles.

`apply_profile_form` est l'unique point d'entrée utilisé par
`client.profile`, `caterer.account` et `admin.profile` pour persister
prénom / nom / email. Le changement d'email exige une **re-authentification
par mot de passe** : pas une déconnexion / reconnexion complète, mais
suffisant pour bloquer un attaquant qui n'aurait que le cookie de
session (cf. PR #76 : la session reste valide après login, le mot de
passe reste la preuve d'identité forte).

Le helper ne commit pas : la transaction reste pilotée par le caller.
"""

from __future__ import annotations

import bcrypt
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select

from models import User
from services.audit import log_admin_action


def apply_profile_form(db, user, form) -> str | None:
    """Applique les champs `first_name`, `last_name`, `email` de `form`
    sur `user`. Retourne un message d'erreur (à `flash` côté caller) en
    cas d'invalidité, ou `None` sur succès. Le caller commit.

    Règles :
      * prénom / nom vides → on garde la valeur existante (UX : un envoi
        partiel ne doit pas effacer une donnée déjà saisie).
      * email vide → on garde la valeur existante.
      * email différent → exige `current_password` correct + pas de
        collision avec un autre compte.
    """
    db.add(user)
    if form.first_name.data is not None:
        user.first_name = (form.first_name.data or "").strip() or user.first_name
    if form.last_name.data is not None:
        user.last_name = (form.last_name.data or "").strip() or user.last_name

    new_email = (form.email.data or "").strip().lower()
    if not new_email or new_email == user.email:
        return None

    # Validation de la syntaxe e-mail uniquement quand l'utilisateur
    # change effectivement la valeur : un POST de changement de nom
    # avec l'email inchangé ne doit pas trébucher ici.
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

    # Pré-check d'unicité : sans ça, l'IntegrityError au commit
    # remonterait en 500 plutôt qu'en flash propre.
    #
    # Anti-énumération : un attaquant disposant d'un mot de passe valide
    # sur SON compte (ou ayant volé sa propre session) pourrait sinon
    # itérer sur des adresses cibles et lire la confirmation d'existence
    # via la distinction "déjà utilisée" vs "OK". On renvoie un message
    # neutre qui ne confirme pas l'existence d'un compte tiers.
    collision = db.scalar(
        select(User.id).where(User.email == new_email, User.id != user.id)
    )
    if collision:
        return "Cette adresse e-mail ne peut pas être utilisée pour ce compte."

    # Trace auto-mutation sensible : changement d'adresse e-mail.
    # On enregistre l'ancienne et la nouvelle adresse dans `extra` pour
    # qu'un audit puisse reconstituer l'historique sans relire toutes
    # les colonnes. L'IP/UA sont capturées automatiquement.
    old_email = user.email
    user.email = new_email
    log_admin_action(
        db,
        user,
        "account.email_change",
        target_type="user",
        target_id=user.id,
        extra={"old_email": old_email, "new_email": new_email},
    )
    return None
