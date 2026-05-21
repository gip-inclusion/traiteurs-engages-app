"""Public legal pages — CGS (Conditions Générales de Services).

Each version's body lives in a Jinja template under `templates/legal/`.
The `TermsVersion` table is the registry: which slug is current, where
its template lives, when it took effect.

Pages are public on purpose: prospective users must read the CGS before
signing up, and existing users (or anyone) can re-read any past version.
"""

from flask import Blueprint, abort, make_response, redirect, render_template, url_for
from sqlalchemy import select

from database import get_db
from models import TermsVersion
from services.terms import current_terms_version


legal_bp = Blueprint("legal", __name__)


# Mirrors inclusion.gouv.fr/.well-known/security.txt — same operator (GIP
# Plateforme de l'inclusion), same security inbox. Renew `Expires` < 1 year
# before the deadline (the GIP rotates it on inclusion.gouv.fr — keep in sync).
_SECURITY_TXT = (
    "Contact: mailto:security@inclusion.gouv.fr\n"
    "Policy: https://inclusion.gouv.fr/.well-known/security-policy.txt\n"
    "Preferred-Languages: fr, en\n"
    "Expires: 2027-04-01T00:00:00.000Z\n"
    "Encryption: https://inclusion.gouv.fr/.well-known/pdi-pgp.asc\n"
    "Canonical: https://les-traiteurs-engages.fr/.well-known/security.txt\n"
)


@legal_bp.route("/.well-known/security.txt")
def security_txt():
    """RFC 9116 security disclosure file."""
    response = make_response(_SECURITY_TXT, 200)
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@legal_bp.route("/cgs")
def cgs_current():
    """Redirect to the currently-in-force CGS version.

    The 302 (not a direct render) keeps URLs version-stable: a link
    saved today points at /cgs/v1; once /cgs/v2 ships, the new visitors
    land there but the saved link still resolves correctly.
    """
    db = get_db()
    return redirect(url_for("legal.cgs_by_slug", slug=current_terms_version(db).slug))


@legal_bp.route("/cgs/<slug>")
def cgs_by_slug(slug: str):
    db = get_db()
    version = db.scalar(select(TermsVersion).where(TermsVersion.slug == slug))
    if version is None:
        abort(404)
    return render_template(version.template_name, version=version)
