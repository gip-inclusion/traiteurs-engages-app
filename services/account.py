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
      * prénom / nom / email obligatoires (les 3 templates marquent les
        champs `required` — on refuse un POST scripté qui les blanchirait
        silencieusement).
      * email inchangé → on s'arrête là (pas de re-auth, pas d'audit).
      * email différent → exige `current_password` correct + pas de
        collision avec un autre compte.
    """
    first_name = (form.first_name.data or "").strip()
    last_name = (form.last_name.data or "").strip()
    new_email = (form.email.data or "").strip().lower()
    if not first_name or not last_name or not new_email:
        return "Prénom, nom et adresse e-mail sont obligatoires."

    user.first_name = first_name
    user.last_name = last_name

    # Comparaison case-insensitive : un compte historique stocké en casse
    # mixte ne doit pas trébucher si l'utilisateur retape son email tel
    # quel (sinon : re-auth inutile + audit log parasite).
    if new_email == (user.email or "").lower():
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
    # On ne snapshot PAS l'ancienne / nouvelle adresse dans `extra` :
    # la rétention d'`audit_logs` peut différer de celle de `users`, et
    # dupliquer la PII y crée une zone d'effacement parallèle à gérer
    # côté RGPD. `actor_id` + `actor_email` (snapshot au moment du log)
    # + `target_id` + IP/UA suffisent pour la forensique.
    user.email = new_email
    log_admin_action(
        db,
        user,
        "account.email_change",
        target_type="user",
        target_id=user.id,
    )
    return None
