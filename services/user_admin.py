"""Helpers pour la gestion des comptes utilisateurs côté super_admin.

Centralise les validations et les mutations destructrices pour qu'elles
soient testables hors du contexte Flask et appelables par les routes
`admin.user_*`. Les fonctions `can_*` ne mutent pas et renvoient un
message d'erreur (en français, prêt à `flash`) si l'action est refusée,
ou `None` si elle est autorisée.

Deux cas usables côté admin :

* **Suppression d'un compte mal créé.** Refusée tant que l'utilisateur
  a contribué quoi que ce soit de structurant (devis, employés
  rattachés, avis client). Sinon on nettoie les rows annexes
  (notifications, tokens de reset, audit_logs.actor_id) avant le delete
  pour ne pas heurter les FKs non-nullables.

* **Conversion `client_*` → `caterer`.** Détache la `Company` (et la
  supprime si elle est orpheline), crée une fiche `Caterer` minimale
  (`is_validated=False` — l'admin la validera ensuite via
  `/admin/caterers/<id>/validate`) et bascule `role` + `caterer_id`.
  Refusée si le user a déjà un historique métier (mêmes critères que
  la suppression) parce qu'on ne saurait pas où rattacher les rows
  existantes après bascule.

Les routes commitent ; les helpers ne commitent pas (laissent la
transaction au caller, cohérent avec le reste du codebase — cf.
`services.account.apply_profile_form`).
"""

from __future__ import annotations

from sqlalchemy import func, select

from models import (
    Caterer,
    CatererReview,
    Company,
    CompanyEmployee,
    Message,
    Notification,
    PasswordResetToken,
    QuoteRequest,
    User,
    UserRole,
)


def _user_metrics(db, user: User) -> dict[str, int]:
    """Comptes des rows liées au user — informationnel + base des
    refus de delete/convert."""
    counts = {
        "quote_requests": db.scalar(
            select(func.count(QuoteRequest.id)).where(QuoteRequest.user_id == user.id)
        )
        or 0,
        "messages_sent": db.scalar(
            select(func.count(Message.id)).where(Message.sender_id == user.id)
        )
        or 0,
        "messages_received": db.scalar(
            select(func.count(Message.id)).where(Message.recipient_id == user.id)
        )
        or 0,
        "employees": db.scalar(
            select(func.count(CompanyEmployee.id)).where(
                CompanyEmployee.user_id == user.id
            )
        )
        or 0,
        "reviews": db.scalar(
            select(func.count(CatererReview.id)).where(
                CatererReview.reviewer_user_id == user.id
            )
        )
        or 0,
    }
    return counts


def user_metrics(db, user: User) -> dict[str, int]:
    """Version exportée — réutilisée par la page détail pour afficher
    le contexte au super_admin avant qu'il déclenche une action."""
    return _user_metrics(db, user)


def _has_business_history(metrics: dict[str, int]) -> bool:
    """Un compte « vraiment vide » = aucune QR, aucun message, pas
    d'employé rattaché, pas d'avis. Notifications et tokens de reset
    ne comptent pas — ils sont système, pas métier."""
    return any(
        metrics[k] > 0
        for k in (
            "quote_requests",
            "messages_sent",
            "messages_received",
            "employees",
            "reviews",
        )
    )


def _is_last_super_admin(db, user: User) -> bool:
    """True si supprimer/convertir `user` retire le dernier super_admin
    de la base — on refuse pour ne pas se locker hors de l'interface."""
    if user.role != UserRole.super_admin:
        return False
    other = db.scalar(
        select(func.count(User.id)).where(
            User.role == UserRole.super_admin, User.id != user.id
        )
    )
    return (other or 0) == 0


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------


def can_delete_user(db, user: User, *, actor: User) -> str | None:
    """`None` si la suppression est autorisée, sinon un message
    d'erreur prêt à `flash`."""
    if user.id == actor.id:
        return "Vous ne pouvez pas supprimer votre propre compte."
    if _is_last_super_admin(db, user):
        return (
            "Impossible de supprimer le dernier super administrateur. "
            "Promouvez un autre compte d'abord."
        )
    metrics = _user_metrics(db, user)
    if _has_business_history(metrics):
        return (
            "Ce compte a un historique métier (devis, messages, employé "
            "rattaché ou avis). Suppression refusée par sécurité — passez "
            "par la CLI Scalingo pour les cas complexes."
        )
    return None


def delete_user(db, user: User) -> None:
    """Supprime le user et nettoie les rows annexes qui n'ont pas de
    cascade DB. Présume que `can_delete_user` a déjà été appelée et a
    renvoyé `None`. Le caller commit.

    Cascade applicative :
      * `notifications` : delete (pas d'usage post-mortem)
      * `password_reset_tokens` : delete (sinon FK violation)
      * `audit_logs.actor_id` : laissé tel quel — la colonne est
        nullable et on préserve l'audit trail. Postgres set NULL sur
        delete grâce à la nullable FK sans ON DELETE explicite ? Non,
        en fait, par défaut sans ON DELETE, c'est NO ACTION → on
        nettoie via UPDATE explicite ci-dessous.
      * `companies` : si la Company associée n'a plus aucun user
        rattaché après cette suppression, on la supprime aussi
        (Company orpheline créée à l'inscription).
    """
    # Notifications (FK NOT NULL → pas de SET NULL possible)
    db.execute(Notification.__table__.delete().where(Notification.user_id == user.id))
    # Tokens de reset (FK NOT NULL)
    db.execute(
        PasswordResetToken.__table__.delete().where(
            PasswordResetToken.user_id == user.id
        )
    )
    # Audit logs : on garde la trace, on neutralise juste l'acteur.
    # Import tardif pour éviter le cycle services.audit ↔ services.user_admin.
    from models import AuditLog

    db.execute(
        AuditLog.__table__.update()
        .where(AuditLog.actor_id == user.id)
        .values(actor_id=None)
    )

    # Company orpheline ?
    company_id = user.company_id
    db.delete(user)
    db.flush()  # propager le delete avant la requête de comptage

    if company_id:
        remaining = db.scalar(
            select(func.count(User.id)).where(User.company_id == company_id)
        )
        if (remaining or 0) == 0:
            company = db.get(Company, company_id)
            if company is not None:
                db.delete(company)


# ---------------------------------------------------------------------------
# Conversion client → traiteur
# ---------------------------------------------------------------------------


_CONVERTIBLE_ROLES = {UserRole.client_admin, UserRole.client_user}


def can_convert_to_caterer(db, user: User, *, actor: User) -> str | None:
    """Mêmes garde-fous que la suppression + filtre sur le rôle de
    départ (seul un compte client peut basculer vers caterer)."""
    if user.id == actor.id:
        return "Vous ne pouvez pas modifier votre propre compte."
    if user.role not in _CONVERTIBLE_ROLES:
        return (
            "Seul un compte client peut être converti en traiteur. "
            f"Rôle actuel : {user.role}."
        )
    metrics = _user_metrics(db, user)
    if _has_business_history(metrics):
        return (
            "Ce compte a un historique métier — la conversion laisserait "
            "des devis/messages côté client. Refus."
        )
    return None


def convert_to_caterer(
    db,
    user: User,
    *,
    caterer_name: str,
    caterer_siret: str,
    structure_type,
    invoice_prefix: str,
) -> Caterer:
    """Détache la Company (delete si orpheline), crée une fiche
    `Caterer` minimale, bascule le user. Présume `can_convert_to_caterer`
    OK. Le caller commit.

    La fiche traiteur reste `is_validated=False` : le super_admin doit
    encore la valider via `/admin/caterers/<id>/validate` après que le
    traiteur ait complété son profil (logo, photos, prestations…)."""
    # Détacher de la Company
    company_id = user.company_id
    user.company_id = None
    user.membership_status = None
    if company_id:
        db.flush()
        remaining = db.scalar(
            select(func.count(User.id)).where(User.company_id == company_id)
        )
        if (remaining or 0) == 0:
            company = db.get(Company, company_id)
            if company is not None:
                db.delete(company)

    # Créer la fiche traiteur minimale
    caterer = Caterer(
        name=caterer_name.strip(),
        siret=caterer_siret.strip(),
        structure_type=structure_type,
        invoice_prefix=invoice_prefix.strip().upper(),
        is_validated=False,
    )
    db.add(caterer)
    db.flush()  # nécessaire pour avoir caterer.id avant l'attribution

    user.role = UserRole.caterer
    user.caterer_id = caterer.id
    return caterer
