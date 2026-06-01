import datetime
import hashlib
import secrets
import uuid

from flask import flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import func, select

from blueprints.client._helpers import own_service_id
from blueprints.middleware import login_required, role_required
from blueprints.scoping import (
    get_company_employee,
    get_company_service,
    get_pending_user,
)
from database import get_db
from extensions import limiter
from forms.client import EmployeeForm, ServiceForm
from models import Company, CompanyEmployee, CompanyService, MembershipStatus, User
from services.notifications import notify
from services.password_reset import revoke_all_sessions

# Invite expires this many days after invited_at; rejected on redemption.
INVITE_TOKEN_TTL_DAYS = 7


def _hash_invite_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stash_invite_raw(employee_id, raw: str) -> None:
    # One-shot store popped by /team so the raw token doesn't outlive the
    # redirect that surfaces the copy-paste modal.
    session[f"invite_raw:{employee_id}"] = raw


def register(bp):
    @bp.route("/team")
    @login_required
    @role_required("client_admin")
    def team():
        user = g.current_user
        db = get_db()
        services = db.scalars(
            select(CompanyService).where(CompanyService.company_id == user.company_id)
        ).all()
        employees = db.scalars(
            select(CompanyEmployee).where(CompanyEmployee.company_id == user.company_id)
        ).all()
        pending_users = db.scalars(
            select(User).where(
                User.company_id == user.company_id,
                User.membership_status == MembershipStatus.pending,
            )
        ).all()

        # ?invite=<employee_id> auto-opens the copy-paste modal after a
        # fresh create/rotate. Only honoured for the admin's own company.
        invite_employee = None
        invite_raw_token = None
        invite_id = request.args.get("invite")
        if invite_id:
            try:
                invite_uuid = uuid.UUID(invite_id)
            except ValueError:
                invite_uuid = None
            if invite_uuid is not None:
                invite_employee = db.scalar(
                    select(CompanyEmployee).where(
                        CompanyEmployee.id == invite_uuid,
                        CompanyEmployee.company_id == user.company_id,
                        CompanyEmployee.invite_token.is_not(None),
                        CompanyEmployee.user_id.is_(None),
                    )
                )
                # Pop so a refresh can't replay the modal; the DB only
                # holds the digest, not the raw token.
                if invite_employee is not None:
                    invite_raw_token = session.pop(
                        f"invite_raw:{invite_employee.id}", None
                    )

        return render_template(
            "client/team.html",
            user=user,
            services=services,
            employees=employees,
            pending_users=pending_users,
            invite_employee=invite_employee,
            invite_raw_token=invite_raw_token,
        )

    @bp.route("/team/services", methods=["POST"])
    @login_required
    @role_required("client_admin")
    def team_service_create():
        user = g.current_user
        form = ServiceForm()
        if not form.validate_on_submit():
            flash("Le nom du service est obligatoire.", "error")
            return redirect(url_for("client.team"))
        db = get_db()
        service = CompanyService(
            company_id=user.company_id,
            name=form.name.data.strip(),
            description=(form.description.data or "").strip() or None,
            annual_budget=form.annual_budget.data,
        )
        db.add(service)
        db.commit()
        flash("Service cree.", "success")
        return redirect(url_for("client.team"))

    @bp.route("/team/services/<uuid:service_id>/edit", methods=["POST"])
    @login_required
    @role_required("client_admin")
    def team_service_edit(service_id):
        user = g.current_user
        db = get_db()
        service = get_company_service(service_id, user.company_id)
        form = ServiceForm()
        if not form.validate_on_submit():
            flash("Le nom du service est obligatoire.", "error")
            return redirect(url_for("client.team"))
        service.name = form.name.data.strip()
        service.description = (form.description.data or "").strip() or None
        service.annual_budget = form.annual_budget.data
        db.commit()
        flash("Service mis a jour.", "success")
        return redirect(url_for("client.team"))

    @bp.route("/team/services/<uuid:service_id>/delete", methods=["POST"])
    @login_required
    @role_required("client_admin")
    def team_service_delete(service_id):
        user = g.current_user
        db = get_db()
        service = get_company_service(service_id, user.company_id)
        employee_count = db.scalar(
            select(func.count(CompanyEmployee.id)).where(
                CompanyEmployee.service_id == service_id
            )
        )
        if employee_count > 0:
            flash(
                "Impossible de supprimer un service auquel des employes sont rattaches.",
                "error",
            )
            return redirect(url_for("client.team"))
        db.delete(service)
        db.commit()
        flash("Service supprime.", "success")
        return redirect(url_for("client.team"))

    @bp.route("/team/employees", methods=["POST"])
    @login_required
    @role_required("client_admin")
    def team_employee_create():
        # Creating implicitly invites — no mail pipeline yet, so the admin
        # gets the signup URL back via the modal.
        user = g.current_user
        form = EmployeeForm()
        if not form.validate_on_submit():
            flash("Prenom, nom et email sont obligatoires.", "error")
            return redirect(url_for("client.team"))
        db = get_db()
        email = form.email.data.strip().lower()
        # Duplicate rows are never redeemable (User.email is globally unique).
        duplicate = db.scalar(
            select(CompanyEmployee).where(
                CompanyEmployee.company_id == user.company_id,
                CompanyEmployee.email == email,
            )
        )
        if duplicate:
            flash(
                "Un collaborateur avec cette adresse e-mail existe deja.",
                "error",
            )
            return redirect(url_for("client.team"))
        # Persist only the digest; rotate via /team/employees/<id>/invite
        # to regenerate a fresh raw token.
        raw_token = secrets.token_urlsafe(32)
        employee = CompanyEmployee(
            company_id=user.company_id,
            first_name=form.first_name.data.strip(),
            last_name=form.last_name.data.strip(),
            email=email,
            position=(form.position.data or "").strip() or None,
            service_id=own_service_id(db, user, form.service_id.data),
            invite_token=_hash_invite_token(raw_token),
            invited_at=datetime.datetime.utcnow(),
        )
        db.add(employee)
        db.commit()
        _stash_invite_raw(employee.id, raw_token)
        return redirect(url_for("client.team", invite=str(employee.id)))

    @bp.route("/team/employees/<uuid:employee_id>/edit", methods=["POST"])
    @login_required
    @role_required("client_admin")
    def team_employee_edit(employee_id):
        user = g.current_user
        db = get_db()
        employee = get_company_employee(employee_id, user.company_id)
        form = EmployeeForm()
        if not form.validate_on_submit():
            flash("Prenom, nom et email sont obligatoires.", "error")
            return redirect(url_for("client.team"))
        employee.first_name = form.first_name.data.strip()
        employee.last_name = form.last_name.data.strip()
        new_email = form.email.data.strip().lower()
        if employee.user_id is None and new_email != (employee.email or "").lower():
            employee.invite_token = None
            employee.invited_at = None
        employee.email = new_email
        employee.position = (form.position.data or "").strip() or None
        employee.service_id = own_service_id(db, user, form.service_id.data)
        db.commit()
        flash("Employe mis a jour.", "success")
        return redirect(url_for("client.team"))

    @bp.route("/team/employees/<uuid:employee_id>/delete", methods=["POST"])
    @login_required
    @role_required("client_admin")
    def team_employee_delete(employee_id):
        user = g.current_user
        db = get_db()
        employee = get_company_employee(employee_id, user.company_id)
        # Defence in depth against a replayed POST despite the UI hiding
        # the trash button for self-rows.
        if employee.user_id == user.id:
            flash("Vous ne pouvez pas vous retirer vous-même des effectifs.", "error")
            return redirect(url_for("client.team"))
        if employee.user_id:
            target = db.get(User, employee.user_id)
            if target is not None and target.company_id == user.company_id:
                target.membership_status = MembershipStatus.rejected
                target.company_id = None
                revoke_all_sessions(db, target)
        db.delete(employee)
        db.commit()
        flash("Employe supprime.", "success")
        return redirect(url_for("client.team"))

    @bp.route("/team/employees/<uuid:employee_id>/invite", methods=["POST"])
    @login_required
    @role_required("client_admin")
    @limiter.limit("10 per minute")
    def team_employee_invite(employee_id):
        # Re-invoking rotates the token (after leak or lost URL).
        user = g.current_user
        db = get_db()
        employee = get_company_employee(employee_id, user.company_id)
        if employee.user_id is not None:
            flash(
                "Ce collaborateur a deja un compte; aucune invitation necessaire.",
                "info",
            )
            return redirect(url_for("client.team"))
        # 32 urlsafe bytes ≈ 256 bits.
        raw_token = secrets.token_urlsafe(32)
        employee.invite_token = _hash_invite_token(raw_token)
        employee.invited_at = datetime.datetime.utcnow()
        db.commit()
        _stash_invite_raw(employee.id, raw_token)
        flash(
            "Lien d'invitation genere. Copiez-le et envoyez-le a votre collaborateur.",
            "success",
        )
        return redirect(url_for("client.team", invite=str(employee.id)))

    @bp.route("/team/employees/<uuid:employee_id>/invite/revoke", methods=["POST"])
    @login_required
    @role_required("client_admin")
    def team_employee_invite_revoke(employee_id):
        user = g.current_user
        db = get_db()
        employee = get_company_employee(employee_id, user.company_id)
        employee.invite_token = None
        employee.invited_at = None
        db.commit()
        flash("Invitation revoquee.", "info")
        return redirect(url_for("client.team"))

    @bp.route("/team/approve/<uuid:user_id>", methods=["POST"])
    @login_required
    @role_required("client_admin")
    def team_approve(user_id):
        admin = g.current_user
        db = get_db()
        target_user = get_pending_user(user_id, admin.company_id)
        target_user.membership_status = MembershipStatus.active

        # Link any matching pre-created invite row instead of duplicating.
        existing = db.scalar(
            select(CompanyEmployee).where(
                CompanyEmployee.company_id == admin.company_id,
                (
                    (CompanyEmployee.user_id == target_user.id)
                    | (CompanyEmployee.email == target_user.email)
                ),
            )
        )
        if existing:
            existing.user_id = target_user.id
            existing.first_name = target_user.first_name
            existing.last_name = target_user.last_name
            existing.email = target_user.email
            # SIRET signup supersedes any outstanding invite link.
            existing.invite_token = None
            existing.invited_at = None
        else:
            db.add(
                CompanyEmployee(
                    company_id=admin.company_id,
                    first_name=target_user.first_name,
                    last_name=target_user.last_name,
                    email=target_user.email,
                    user_id=target_user.id,
                )
            )

        company = db.get(Company, admin.company_id)
        notify(
            db,
            user_id=target_user.id,
            type="membership_approved",
            title="Bienvenue !",
            body=f"Votre rattachement à {company.name if company else 'votre structure'} a été validé.",
            related_entity_type="company",
            related_entity_id=admin.company_id,
        )

        db.commit()
        flash("Membre approuve et ajoute aux effectifs.", "success")
        return redirect(url_for("client.team"))

    @bp.route("/team/reject/<uuid:user_id>", methods=["POST"])
    @login_required
    @role_required("client_admin")
    def team_reject(user_id):
        admin = g.current_user
        db = get_db()
        target_user = get_pending_user(user_id, admin.company_id)
        target_user.membership_status = MembershipStatus.rejected
        revoke_all_sessions(db, target_user)
        db.commit()
        flash("Membre rejete.", "info")
        return redirect(url_for("client.team"))
