from functools import wraps

from flask import abort, flash, g, jsonify, redirect, request, url_for

from models import UserRole


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not g.get("current_user"):
            flash("Veuillez vous connecter pour acceder a cette page.", "error")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not g.get("current_user"):
                flash("Veuillez vous connecter pour acceder a cette page.", "error")
                return redirect(url_for("auth.login"))
            if g.current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)

        return decorated

    return decorator


def validated_caterer_required(f):
    # No-op for non-caterers so it can decorate shared blueprints. On /api
    # we return JSON 403 instead of a 302 to /caterer/pending — XHR callers
    # can't usefully follow the redirect.
    @wraps(f)
    def decorated(*args, **kwargs):
        user = g.get("current_user")
        if user is None:
            flash("Veuillez vous connecter pour acceder a cette page.", "error")
            return redirect(url_for("auth.login"))
        if user.role != UserRole.caterer:
            return f(*args, **kwargs)
        caterer = user.caterer
        if caterer is None or not caterer.is_validated:
            if request.blueprint == "api":
                return jsonify({"error": "caterer_not_validated"}), 403
            return redirect(url_for("caterer.pending_validation"))
        return f(*args, **kwargs)

    return decorated
