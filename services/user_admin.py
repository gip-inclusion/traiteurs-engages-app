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
        "orders": db.scalar(
            select(func.count(Order.id)).where(Order.client_admin_id == user.id)
        )
        or 0,
    }
    return counts


def _has_business_history(metrics: dict[str, int]) -> bool:
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
    return (
        db.scalar(select(func.count(User.id)).where(User.role == UserRole.super_admin))
        or 0
    )


def _is_last_super_admin(db, user: User) -> bool:
    if user.role != UserRole.super_admin:
        return False
    other = db.scalar(
        select(func.count(User.id)).where(
            User.role == UserRole.super_admin, User.id != user.id
        )
    )
    return (other or 0) == 0


def can_delete_user(db, user: User, *, actor: User) -> str | None:
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
    if not company_id:
        return
    db.flush()
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
    db.execute(
        CompanyEmployee.__table__.delete().where(CompanyEmployee.user_id == user.id)
    )
    db.execute(Notification.__table__.delete().where(Notification.user_id == user.id))
    db.execute(
        PasswordResetToken.__table__.delete().where(
            PasswordResetToken.user_id == user.id
        )
    )
    from models import AuditLog

    db.execute(
        AuditLog.__table__.update()
        .where(AuditLog.actor_id == user.id)
        .values(actor_id=None)
    )

    company_id = user.company_id
    db.delete(user)
    _delete_company_if_orphan(db, company_id)


_MUTABLE_ROLES = {UserRole.client_admin, UserRole.client_user, UserRole.caterer}


def _needs_caterer_info(current_role, new_role) -> bool:
    return current_role != UserRole.caterer and new_role == UserRole.caterer


def _needs_company_info(current_role, new_role) -> bool:
    return current_role == UserRole.caterer and new_role in (
        UserRole.client_admin,
        UserRole.client_user,
    )


def can_change_role(db, user: User, new_role, *, actor: User) -> str | None:
    if user.id == actor.id:
        return "Vous ne pouvez pas modifier votre propre compte."
    if user.role == UserRole.super_admin:
        return "Le rôle d'un super administrateur ne peut pas être modifié ici."
    if new_role not in _MUTABLE_ROLES:
        return f"Rôle cible invalide : {new_role}."
    if user.role == new_role:
        return None
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
    if not _needs_caterer_info(user.role, new_role) and not _needs_company_info(
        user.role, new_role
    ):
        user.role = new_role
        revoke_all_sessions(db, user)
        return

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

    if _needs_company_info(user.role, new_role):
        user.caterer_id = None

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
