import datetime
import hashlib
import logging
import os

import bcrypt
from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from blueprints.middleware import login_required
from database import get_db
from extensions import limiter
from models import (
    Caterer,
    Company,
    CompanyEmployee,
    CompanyService,
    MembershipStatus,
    User,
    UserRole,
)
from services.audit import log_admin_action
from services.notifications import (
    company_admin_user_ids,
    notify_users,
    super_admin_user_ids,
)
from services.password_reset import revoke_all_sessions
from services.slugs import generate_invoice_prefix
from services.terms import current_terms_version, is_terms_accepted

logger = logging.getLogger(__name__)


# Duplicated from the team handler so /signup/invite/<token> can reject
# stale links without importing from blueprints/client (circular import).
INVITE_TOKEN_TTL_DAYS = 7

auth_bp = Blueprint("auth", __name__)

LOGIN_LIMIT = "10 per minute"
# Override via env for local iteration so test loops don't wedge for an hour.
SIGNUP_LIMIT = os.environ.get("SIGNUP_LIMIT", "5 per hour")

# VULN-14: length >= 12 + ≥3 character classes + not in the top-passwords
# blocklist below. Plug in zxcvbn / HIBP later for a stronger check.
PASSWORD_MIN_LENGTH = 12
PASSWORD_BLOCKLIST = {
    "password",
    "password1",
    "password123",
    "passw0rd",
    "motdepasse",
    "azerty",
    "azerty123",
    "qwerty",
    "qwerty123",
    "qwertyuiop",
    "123456",
    "123456789",
    "1234567890",
    "111111",
    "000000",
    "12345678",
    "iloveyou",
    "admin",
    "admin123",
    "letmein",
    "welcome",
    "welcome1",
    "monkey",
    "dragon",
    "abc123",
    "abcdef",
    "changeme",
    "changeme123",
    "secret",
    "test1234",
}

# VULN-102: always pay bcrypt's cost so /login response time is constant
# whether the email exists or not (~250 ms vs ~10 ms otherwise).
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"timing-safe-dummy", bcrypt.gensalt()).decode()


def validate_password(password: str) -> str | None:
    if len(password) < PASSWORD_MIN_LENGTH:
        return (
            f"Le mot de passe doit comporter au moins {PASSWORD_MIN_LENGTH} caracteres."
        )
    if password.lower() in PASSWORD_BLOCKLIST:
        return "Ce mot de passe est trop courant. Choisissez-en un plus original."
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        return (
            "Le mot de passe doit contenir au moins 3 categories de caracteres "
            "parmi : minuscules, majuscules, chiffres, caracteres speciaux."
        )
    return None


ROLE_DASHBOARDS = {
    UserRole.client_admin: "client.dashboard",
    UserRole.client_user: "client.dashboard",
    UserRole.caterer: "caterer.dashboard",
    UserRole.super_admin: "admin.dashboard",
}


def _stamp_session(user):
    session["user_id"] = str(user.id)
    session["session_epoch"] = (
        user.sessions_invalidated_at.isoformat()
        if user.sessions_invalidated_at
        else None
    )


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(LOGIN_LIMIT, methods=["POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Veuillez remplir tous les champs.", "error")
            return render_template("auth/login.html")
        db = get_db()
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        # VULN-102: dummy hash when user doesn't exist keeps timing constant.
        hash_to_check = user.password_hash if user else _DUMMY_PASSWORD_HASH
        password_ok = bcrypt.checkpw(password.encode(), hash_to_check.encode())
        if not user or not password_ok:
            # No email/user_id in the log: avoids confirming whether the
            # account exists when stdout is grep'd. ip is auto-stamped by
            # ContextFilter, which is enough for brute-force detection.
            logger.warning(
                "login_failed",
                extra={"event": "login_failed", "reason": "bad_credentials"},
            )
            flash("Email ou mot de passe incorrect.", "error")
            return render_template("auth/login.html")
        # Audit H-2: one opaque message for every inactive state so an
        # attacker with a leaked password can't map HR status / confirm
        # SIRET-membership matches. The real reason still lands in logs.
        inactive_membership = user.membership_status in (
            MembershipStatus.pending,
            MembershipStatus.rejected,
        )
        if not user.is_active or inactive_membership:
            # membership_status round-trips as str OR MembershipStatus
            # depending on whether it came from DB or memory; getattr
            # collapses both to the bare token without leaking
            # "MembershipStatus.pending" into logs.
            logger.info(
                "login refused for non-active account",
                extra={
                    "user_id": str(user.id),
                    "is_active": user.is_active,
                    "membership_status": (
                        getattr(user.membership_status, "value", user.membership_status)
                        if user.membership_status
                        else None
                    ),
                },
            )
            flash(
                "Connexion impossible. Contactez l'administrateur de votre structure.",
                "error",
            )
            return render_template("auth/login.html")
        # Rotate the session on successful auth: drop pre-login state
        # before issuing the authenticated cookie.
        session.clear()
        _stamp_session(user)
        session.permanent = True
        logger.info(
            "login_success",
            extra={
                "event": "login_success",
                "user_id": str(user.id),
                "role": getattr(user.role, "value", str(user.role)),
            },
        )
        endpoint = ROLE_DASHBOARDS.get(UserRole(user.role), "client.dashboard")
        return redirect(url_for(endpoint))
    return render_template("auth/login.html")


@auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit(SIGNUP_LIMIT, methods=["POST"])
def signup():
    if request.method == "POST":
        role = request.form.get("role", "")
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        siret = request.form.get("siret", "").strip()
        accept_terms = is_terms_accepted(request.form)

        if not all([role, email, password, first_name, last_name, siret]):
            flash("Veuillez remplir tous les champs obligatoires.", "error")
            return render_template("auth/signup.html")

        if not accept_terms:
            flash(
                "Vous devez accepter les conditions générales de services pour "
                "créer un compte.",
                "error",
            )
            return render_template("auth/signup.html")

        if len(siret) != 14 or not siret.isdigit():
            flash("Le SIRET doit comporter exactement 14 chiffres.", "error")
            return render_template("auth/signup.html")

        password_error = validate_password(password)
        if password_error:
            flash(password_error, "error")
            return render_template("auth/signup.html")

        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        db = get_db()
        # Resolve once so every code path records the same id + timestamp.
        active_terms = current_terms_version(db)
        accepted_at = datetime.datetime.utcnow()

        # VULN-28: always run both lookups so timing doesn't disclose
        # whether email vs SIRET already exists.
        existing_user = db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        existing_company = db.execute(
            select(Company).where(Company.siret == siret)
        ).scalar_one_or_none()

        if existing_user:
            flash(
                "Inscription impossible avec ces informations. "
                "Si vous avez deja un compte, connectez-vous.",
                "error",
            )
            return render_template("auth/signup.html")

        if role == "client_admin":
            if existing_company:
                user = User(
                    email=email,
                    password_hash=password_hash,
                    first_name=first_name,
                    last_name=last_name,
                    role=UserRole.client_user,
                    company_id=existing_company.id,
                    membership_status=MembershipStatus.pending,
                    terms_accepted_version_id=active_terms.id,
                    terms_accepted_at=accepted_at,
                )
                db.add(user)
                db.flush()
                notify_users(
                    db,
                    company_admin_user_ids(db, existing_company.id),
                    type="pending_membership",
                    title="Demande de rattachement",
                    body=f"{first_name} {last_name} ({email}) souhaite rejoindre votre structure.",
                    related_entity_type="user",
                    related_entity_id=user.id,
                )
                db.commit()
                # VULN-28: never confirm SIRET presence, never issue a session.
                # A pending user with a session could otherwise read the
                # company's private demands/orders/messages while waiting.
                flash(
                    "Votre demande de rattachement a ete enregistree. "
                    "L'administrateur de votre structure a ete informe. "
                    "Vous pourrez vous connecter une fois votre acces "
                    "approuve.",
                    "info",
                )
                return redirect(url_for("auth.login"))

            # Company.name is non-nullable; SIRET stands in until the admin
            # renames it via /client/settings.
            company = Company(name=siret, siret=siret)
            db.add(company)
            db.flush()
            user = User(
                email=email,
                password_hash=password_hash,
                first_name=first_name,
                last_name=last_name,
                role=UserRole.client_admin,
                company_id=company.id,
                membership_status=MembershipStatus.active,
                terms_accepted_version_id=active_terms.id,
                terms_accepted_at=accepted_at,
            )
            db.add(user)
            db.flush()

            direction_service = CompanyService(
                company_id=company.id,
                name="Direction",
            )
            db.add(direction_service)
            db.flush()
            db.add(
                CompanyEmployee(
                    company_id=company.id,
                    service_id=direction_service.id,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    position="Administrateur",
                    user_id=user.id,
                )
            )
            db.commit()

            _stamp_session(user)
            from services import email_triggers

            email_triggers.welcome_signup(
                user, role_kind="client", cta_path="/client/settings"
            )
            flash(
                "Bienvenue ! Pour finaliser la création de votre espace, "
                "complétez les paramètres de votre structure.",
                "success",
            )
            return redirect(url_for("client.settings"))

        elif role == "caterer":
            caterer_name = request.form.get("caterer_name", "").strip()
            structure_type = request.form.get("structure_type", "").strip()
            address = request.form.get("address", "").strip()
            city = request.form.get("city", "").strip()
            zip_code = request.form.get("zip_code", "").strip()

            if not all([caterer_name, structure_type]):
                flash(
                    "Le nom du traiteur et le type de structure sont obligatoires.",
                    "error",
                )
                return render_template("auth/signup.html")

            invoice_prefix = generate_invoice_prefix(db)
            caterer = Caterer(
                name=caterer_name,
                siret=siret,
                structure_type=structure_type,
                address=address or None,
                city=city or None,
                zip_code=zip_code or None,
                invoice_prefix=invoice_prefix,
            )
            db.add(caterer)
            db.flush()
            user = User(
                email=email,
                password_hash=password_hash,
                first_name=first_name,
                last_name=last_name,
                role=UserRole.caterer,
                caterer_id=caterer.id,
                membership_status=MembershipStatus.active,
                terms_accepted_version_id=active_terms.id,
                terms_accepted_at=accepted_at,
            )
            db.add(user)
            db.flush()
            notify_users(
                db,
                super_admin_user_ids(db),
                type="caterer_pending_validation",
                title="Nouveau traiteur en attente",
                body=f"{caterer_name} ({structure_type}) attend votre validation.",
                related_entity_type="caterer",
                related_entity_id=caterer.id,
            )
            db.commit()
            _stamp_session(user)
            from services import email_triggers

            email_triggers.welcome_signup(
                user, role_kind="caterer", cta_path="/caterer/profile"
            )
            flash("Votre compte traiteur a ete cree avec succes.", "success")
            return redirect(url_for("caterer.dashboard"))

        else:
            flash("Type de compte invalide.", "error")
            return render_template("auth/signup.html")

    return render_template("auth/signup.html")


def _resolve_invite(token: str) -> CompanyEmployee | None:
    # Column stores SHA-256(token) so a DB leak exposes only digests.
    if not token:
        return None
    db = get_db()
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    employee = db.scalar(
        select(CompanyEmployee).where(CompanyEmployee.invite_token == digest)
    )
    if employee is None:
        return None
    if employee.user_id is not None:
        return None
    if employee.invited_at is not None:
        age = datetime.datetime.utcnow() - employee.invited_at
        if age.days >= INVITE_TOKEN_TTL_DAYS:
            return None
    return employee


@auth_bp.route("/signup/invite/<token>", methods=["GET", "POST"])
@limiter.limit(SIGNUP_LIMIT, methods=["POST"])
def signup_invite(token: str):
    # Bypasses the SIRET pending-approval flow because the token itself
    # proves an admin already trusts the recipient. Single-use, expires
    # after INVITE_TOKEN_TTL_DAYS.
    employee = _resolve_invite(token)
    if employee is None:
        return render_template("auth/signup_invite_invalid.html"), 404

    if request.method == "POST":
        password = request.form.get("password", "")
        accept_terms = is_terms_accepted(request.form)
        if not password:
            flash("Veuillez renseigner un mot de passe.", "error")
            return render_template(
                "auth/signup_invite.html", token=token, employee=employee
            )
        password_error = validate_password(password)
        if password_error:
            flash(password_error, "error")
            return render_template(
                "auth/signup_invite.html", token=token, employee=employee
            )
        if not accept_terms:
            flash(
                "Vous devez accepter les conditions générales de services pour "
                "créer un compte.",
                "error",
            )
            return render_template(
                "auth/signup_invite.html", token=token, employee=employee
            )

        db = get_db()
        active_terms = current_terms_version(db)
        # Email + name come from the CompanyEmployee row the admin pre-filled,
        # never from the POST body, so a tampered submission can't smuggle
        # different values.
        existing_user = db.scalar(select(User).where(User.email == employee.email))
        if existing_user:
            flash(
                "Un compte existe deja avec cette adresse e-mail. Connectez-vous.",
                "error",
            )
            return redirect(url_for("auth.login"))

        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        new_user = User(
            email=employee.email,
            password_hash=password_hash,
            first_name=employee.first_name,
            last_name=employee.last_name,
            role=UserRole.client_user,
            company_id=employee.company_id,
            membership_status=MembershipStatus.active,
            terms_accepted_version_id=active_terms.id,
            terms_accepted_at=datetime.datetime.utcnow(),
        )
        db.add(new_user)

        try:
            db.flush()
            employee.user_id = new_user.id
            employee.invite_token = None
            db.commit()
        except IntegrityError:
            # Race against a parallel signup grabbing the same email.
            db.rollback()
            flash(
                "Un compte existe deja avec cette adresse e-mail. Connectez-vous.",
                "error",
            )
            return redirect(url_for("auth.login"))

        session.clear()
        _stamp_session(new_user)
        session.permanent = True
        from services import email_triggers

        email_triggers.welcome_signup(
            new_user, role_kind="client", cta_path="/client/dashboard"
        )
        flash("Bienvenue ! Votre compte est cree.", "success")
        return redirect(url_for("client.dashboard"))

    return render_template("auth/signup_invite.html", token=token, employee=employee)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    user = g.get("current_user")
    if user is not None:
        logger.info(
            "logout", extra={"event": "logout", "user_id": str(user.id)}
        )
        db = get_db()
        revoke_all_sessions(db, user)
        db.commit()
    session.clear()
    return redirect(url_for("auth.login"))


FORGOT_LIMIT = "5 per hour"
RESET_LIMIT = "5 per hour"


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit(FORGOT_LIMIT, methods=["POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("auth/forgot_password.html")

    from services.password_reset import kick_off_reset

    email = (request.form.get("email") or "").strip()
    db = get_db()
    kick_off_reset(db, email=email)
    db.commit()
    # No email in the log: kick_off_reset is intentionally opaque about
    # whether the address mapped to a user (audit anti-enumeration), and
    # we keep stdout consistent with that.
    logger.info("password_reset_requested", extra={"event": "password_reset_requested"})
    return render_template("auth/forgot_password_sent.html", email=email)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit(RESET_LIMIT, methods=["POST"])
def reset_password(token):
    from services.password_reset import ResetTokenInvalid, consume_token

    if request.method == "GET":
        return render_template("auth/reset_password.html", token=token)

    new_password = request.form.get("password") or ""
    confirm = request.form.get("password_confirm") or ""
    if new_password != confirm:
        flash("Les deux mots de passe ne correspondent pas.", "error")
        return render_template("auth/reset_password.html", token=token), 400

    err = validate_password(new_password)
    if err:
        flash(err, "error")
        return render_template("auth/reset_password.html", token=token), 400

    db = get_db()
    try:
        user = consume_token(db, raw_token=token, new_password=new_password)
    except ResetTokenInvalid:
        logger.warning(
            "password_reset_invalid_token",
            extra={"event": "password_reset_invalid_token"},
        )
        flash(
            "Ce lien de réinitialisation est invalide ou a expiré. "
            "Demandez-en un nouveau.",
            "error",
        )
        return redirect(url_for("auth.forgot_password"))
    db.commit()
    logger.info(
        "password_reset_completed",
        extra={"event": "password_reset_completed", "user_id": str(user.id)},
    )
    flash("Votre mot de passe a été mis à jour. Vous pouvez vous connecter.", "success")
    return redirect(url_for("auth.login"))


CHANGE_PASSWORD_LIMIT = "10 per minute"


@auth_bp.route("/account/change-password", methods=["GET", "POST"])
@limiter.limit(CHANGE_PASSWORD_LIMIT, methods=["POST"])
@login_required
def change_password():
    user = g.current_user

    if request.method == "GET":
        return render_template("auth/change_password.html")

    current = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm = request.form.get("new_password_confirm") or ""

    if not bcrypt.checkpw(current.encode(), user.password_hash.encode()):
        flash("Mot de passe actuel incorrect.", "error")
        return render_template("auth/change_password.html"), 400

    if new_password != confirm:
        flash("Les deux mots de passe ne correspondent pas.", "error")
        return render_template("auth/change_password.html"), 400

    err = validate_password(new_password)
    if err:
        flash(err, "error")
        return render_template("auth/change_password.html"), 400

    if bcrypt.checkpw(new_password.encode(), user.password_hash.encode()):
        flash("Le nouveau mot de passe doit être différent de l'actuel.", "error")
        return render_template("auth/change_password.html"), 400

    db = get_db()
    db.add(user)
    user.password_hash = bcrypt.hashpw(
        new_password.encode("utf-8"), bcrypt.gensalt()
    ).decode()
    revoke_all_sessions(db, user)
    log_admin_action(
        db,
        user,
        "account.password_change",
        target_type="user",
        target_id=user.id,
    )
    db.commit()
    _stamp_session(user)
    flash("Mot de passe mis à jour.", "success")
    return redirect(url_for("auth.change_password"))
