# Dev account switcher gated TWICE: blueprint only registered when
# ENABLE_DEMO_SEED=1, and the email allowlist below restricts impersonation
# to the seven seeded demo accounts even if the flag ever leaked into prod.
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, request, session, url_for
from sqlalchemy import select

from database import get_db
from extensions import limiter
from models import User

devtools_bp = Blueprint("devtools", __name__, url_prefix="/dev")

DEMO_ACCOUNTS = [
    {
        "email": "admin@traiteurs-engages.fr",
        "label": "Super Admin",
        "role": "super_admin",
    },
    {
        "email": "alice@acme-solutions.fr",
        "label": "Alice (Acme)",
        "role": "client_admin",
    },
    {"email": "bob@techcorp.fr", "label": "Bob (TechCorp)", "role": "client_admin"},
    {
        "email": "claire@acme-solutions.fr",
        "label": "Claire (Acme)",
        "role": "client_user",
    },
    {
        "email": "contact@saveurs-solidaires.fr",
        "label": "ESAT Saveurs",
        "role": "caterer",
    },
    {"email": "contact@traiteur-co.fr", "label": "EA Traiteur & Co", "role": "caterer"},
    {
        "email": "contact@delices-engages.fr",
        "label": "EI Delices Engages",
        "role": "caterer",
    },
    {
        "email": "contact@marmites-du-sud.fr",
        "label": "EI Marmites du Sud",
        "role": "caterer",
    },
]
_DEMO_EMAILS = {a["email"] for a in DEMO_ACCOUNTS}


@devtools_bp.route("/switch-account", methods=["POST"])
@limiter.exempt
def switch_account():
    email = (request.form.get("email") or "").strip().lower()
    if email not in _DEMO_EMAILS:
        abort(403)

    db = get_db()
    user = db.scalar(select(User).where(User.email == email))
    if not user:
        flash(f"Compte demo introuvable : {email}.", "error")
        return redirect(url_for("auth.login"))

    # Skip session.clear() (unlike /login): rotating the session also rotates
    # the CSRF token, breaking any tab left open in another dev workflow.
    # ENABLE_DEMO_SEED gates this so VULN-11 doesn't apply.
    session["user_id"] = str(user.id)
    session.permanent = True
    flash(f"[DEV] Connecte en tant que {user.email}.", "info")
    return redirect(url_for("landing"))
