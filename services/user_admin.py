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

* **Changement de rôle.** Bascule entre `client_admin`, `client_user`
  et `caterer`. Pour les bascules qui changent la nature du compte
  (vers/depuis `caterer`), l'admin doit fournir les infos minimales de
  la nouvelle structure (Caterer ou Company). `super_admin` est
  immuable des deux côtés — pas de promotion via cette voie, pas de
  rétrogradation non plus (sinon un admin compromis pourrait
  dégrader le seul super_admin restant). Refusée si le user a un
  historique métier sur le rôle de départ.

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
    CompanyService,
    MembershipStatus,
    Message,
    Notification,
    Order,
    PasswordResetToken,
    QuoteRequest,
    User,
    UserRole,
)
from services.password_reset import revoke_all_sessions


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
        # NOT NULL FK — deleting a user with an order on file would crash
        # the route with IntegrityError, so we surface it as business
        # history instead.
        "orders": db.scalar(
            select(func.count(Order.id)).where(Order.client_admin_id == user.id)
        )
        or 0,
    }
    return counts


def _has_business_history(metrics: dict[str, int]) -> bool:
    """Un compte « vraiment vide » = aucune QR, aucun message, aucun
    avis, aucune commande. Notifications et tokens de reset ne comptent
    pas (système, pas métier).

    `employees` est volontairement EXCLU : tout client rattaché possède
    un row `CompanyEmployee` qui le représente dans sa propre structure
    (créé à l'inscription pour qu'il apparaisse dans /client/team). Le
    compter comme historique métier bloquait à tort la conversion et la
    suppression de quasiment tous les clients. Ce row est nettoyé par
    `_delete_company_if_orphan` au moment de la bascule."""
    return any(
        metrics[k] > 0
        for k in (
            "quote_requests",
            "messages_sent",
            "messages_received",
            "reviews",
            "orders",
        )
    )


def users_with_business_history(db) -> set:
    """Bulk variant of `_has_business_history` for list views: returns
    the set of user IDs that have at least one business-history row.
    Equivalent to OR-ing every `_user_metrics` source > 0, but at a
    fixed cost (one query per source table) instead of 6×N."""
    # NB : `CompanyEmployee.user_id` est volontairement absent — cf.
    # `_has_business_history` (le self-employee n'est pas un historique
    # métier).
    ids: set = set()
    for stmt in (
        select(QuoteRequest.user_id).distinct(),
        select(Message.sender_id).distinct(),
        select(Message.recipient_id).distinct(),
        select(CatererReview.reviewer_user_id).distinct(),
        select(Order.client_admin_id).distinct(),
    ):
        ids.update(db.scalars(stmt).all())
    return ids


def super_admin_count(db) -> int:
    """Cheap one-shot count used by list views to derive the
    last-super-admin guard without recomputing per row."""
    return (
        db.scalar(select(func.count(User.id)).where(User.role == UserRole.super_admin))
        or 0
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
            "Ce compte a un historique métier (devis, messages, avis ou "
            "commandes). Suppression refusée par sécurité — passez par la "
            "CLI Scalingo pour les cas complexes."
        )
    return None


def _delete_company_if_orphan(db, company_id) -> None:
    """Supprime la `Company` si plus aucun user n'y est rattaché.

    Nettoie d'abord les rows enfants qui n'ont pas de cascade DB
    (`CompanyEmployee`, `CompanyService`) — sinon le delete de la
    Company échoue sur une FK NOT NULL. Si des `QuoteRequest` pointent
    encore sur la structure (cas rare : d'anciens membres ont déposé des
    devis), on laisse la Company en place plutôt que de planter — elle
    reste juste détachée, sans utilisateur."""
    if not company_id:
        return
    db.flush()  # propager les détachements (user.company_id = None) avant le comptage
    remaining = db.scalar(
        select(func.count(User.id)).where(User.company_id == company_id)
    )
    if (remaining or 0) > 0:
        return
    qr_count = db.scalar(
        select(func.count(QuoteRequest.id)).where(QuoteRequest.company_id == company_id)
    )
    if (qr_count or 0) > 0:
        return
    db.execute(
        CompanyEmployee.__table__.delete().where(
            CompanyEmployee.company_id == company_id
        )
    )
    db.execute(
        CompanyService.__table__.delete().where(CompanyService.company_id == company_id)
    )
    company = db.get(Company, company_id)
    if company is not None:
        db.delete(company)


def delete_user(db, user: User) -> None:
    """Supprime le user et nettoie les rows annexes qui n'ont pas de
    cascade DB. Présume que `can_delete_user` a déjà été appelée et a
    renvoyé `None`. Le caller commit.

    Cascade applicative :
      * `company_employees` du user : delete AVANT le user (FK
        `user_id` sans ON DELETE → le delete du user échouerait sinon).
      * `notifications`, `password_reset_tokens` : delete (FK NOT NULL).
      * `audit_logs.actor_id` : UPDATE … SET NULL pour préserver
        l'audit trail (FK nullable, mais sans ON DELETE → Postgres
        refuse autrement).
      * `companies` : delete la Company associée si elle devient
        orpheline (Company à 1 user créée à l'inscription).
    """
    # Row employé du user lui-même (FK user_id) — à purger avant le
    # delete du user, sinon ForeignKeyViolation. `_delete_company_if_orphan`
    # nettoiera ensuite les éventuels rows restants de la structure.
    db.execute(
        CompanyEmployee.__table__.delete().where(CompanyEmployee.user_id == user.id)
    )
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
    _delete_company_if_orphan(db, company_id)


# ---------------------------------------------------------------------------
# Changement de rôle
# ---------------------------------------------------------------------------


# Rôles modifiables depuis l'UI. `super_admin` est volontairement absent
# pour éviter qu'un compte admin compromis (ou maladroit) puisse créer
# ou dégrader des super_admins via cette voie. La promotion vers
# super_admin reste manuelle (CLI Scalingo / init_db).
_MUTABLE_ROLES = {UserRole.client_admin, UserRole.client_user, UserRole.caterer}


def _needs_caterer_info(current_role, new_role) -> bool:
    """True si la bascule crée une fiche `Caterer` (X non-caterer →
    caterer). Pas True si on reste caterer ou si on quitte caterer."""
    return current_role != UserRole.caterer and new_role == UserRole.caterer


def _needs_company_info(current_role, new_role) -> bool:
    """True si la bascule crée une fiche `Company` (caterer → client_*
    quand le user n'a pas déjà de `company_id`)."""
    return current_role == UserRole.caterer and new_role in (
        UserRole.client_admin,
        UserRole.client_user,
    )


def can_change_role(db, user: User, new_role, *, actor: User) -> str | None:
    """`None` si la bascule est autorisée, sinon un message d'erreur.
    Couvre les garde-fous communs ; les inputs spécifiques (nom, SIRET)
    sont validés par la route — ils ne sont pas connus à ce stade."""
    if user.id == actor.id:
        return "Vous ne pouvez pas modifier votre propre compte."
    if user.role == UserRole.super_admin:
        return "Le rôle d'un super administrateur ne peut pas être modifié ici."
    if new_role not in _MUTABLE_ROLES:
        return f"Rôle cible invalide : {new_role}."
    if user.role == new_role:
        return None  # no-op, pas une erreur
    metrics = _user_metrics(db, user)
    if _has_business_history(metrics) and (
        _needs_caterer_info(user.role, new_role)
        or _needs_company_info(user.role, new_role)
    ):
        return (
            "Ce compte a un historique métier — changer la nature du compte "
            "(création d'une fiche traiteur ou entreprise) laisserait des "
            "rows orphelines. Refus."
        )
    return None


def change_role(
    db,
    user: User,
    *,
    new_role,
    caterer_name: str | None = None,
    caterer_siret: str | None = None,
    structure_type=None,
    invoice_prefix: str | None = None,
    company_name: str | None = None,
    company_siret: str | None = None,
) -> None:
    """Applique la bascule. Présume `can_change_role` OK et que les
    inputs nécessaires (selon la bascule) sont fournis. Le caller
    commit.

    * Bascule entre `client_admin` ↔ `client_user` : juste `user.role`,
      `company_id` reste pointé sur la même Company.
    * Bascule vers `caterer` : détache Company (delete si orpheline),
      crée une fiche `Caterer` minimale (`is_validated=False`).
    * Bascule `caterer` → `client_*` : la fiche `Caterer` reste (mais
      n'est plus rattachée au user) et on crée une `Company` minimale.
    """
    # Cas trivial — pas de changement de nature
    if not _needs_caterer_info(user.role, new_role) and not _needs_company_info(
        user.role, new_role
    ):
        user.role = new_role
        revoke_all_sessions(db, user)
        return

    # Vers caterer : détacher Company + créer Caterer
    if _needs_caterer_info(user.role, new_role):
        company_id = user.company_id
        user.company_id = None
        user.membership_status = None
        _delete_company_if_orphan(db, company_id)

        caterer = Caterer(
            name=(caterer_name or "").strip(),
            siret=(caterer_siret or "").strip(),
            structure_type=structure_type,
            invoice_prefix=(invoice_prefix or "").strip().upper(),
            is_validated=False,
        )
        db.add(caterer)
        db.flush()
        user.caterer_id = caterer.id
        user.role = new_role
        revoke_all_sessions(db, user)
        return

    # Depuis caterer vers client_* : détacher Caterer + créer Company
    if _needs_company_info(user.role, new_role):
        user.caterer_id = None
        # On laisse la fiche Caterer en base — l'admin la traitera
        # séparément via `/admin/caterers/<id>` si besoin. Plus prudent
        # que de la supprimer automatiquement (elle peut avoir un
        # historique sur d'autres axes).

        company = Company(
            name=(company_name or "").strip(),
            siret=(company_siret or "").strip(),
        )
        db.add(company)
        db.flush()
        user.company_id = company.id
        user.role = new_role
        user.membership_status = MembershipStatus.active
        revoke_all_sessions(db, user)
        return
