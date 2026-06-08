import os
from datetime import timedelta

from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.middleware.proxy_fix import ProxyFix
from whitenoise import WhiteNoise

import config
from config import settings
from database import ScopedSession, engine, get_db
from extensions import csrf, limiter
from logging_config import (
    configure_logging,
    install_request_id_hooks,
    install_sql_tracing,
)
from models import (
    DRINK_LABELS,
    MembershipStatus,
    OrderStatus,
    PaymentStatus,
    QRCStatus,
    QuoteRequestStatus,
    QuoteStatus,
    User,
    UserRole,
)

_BLOCKED_MEMBERSHIP_STATUSES = {MembershipStatus.pending, MembershipStatus.rejected}

configure_logging()
install_sql_tracing(engine)


def _matomo_origin(url: str) -> str:
    # Extrait "https://host[:port]" d'une URL container Matomo, pour
    # n'autoriser dans la CSP que l'origine stricte (pas le path).
    if not url:
        return ""
    scheme_sep = url.find("://")
    if scheme_sep == -1:
        return ""
    after_scheme = url.find("/", scheme_sep + 3)
    return url if after_scheme == -1 else url[:after_scheme]


def _build_csp(matomo_url: str) -> str:
    matomo_origin = _matomo_origin(matomo_url)
    extra = f" {matomo_origin}" if matomo_origin else ""
    return (
        "default-src 'self'; "
        f"script-src 'self'{extra}; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        f"img-src 'self' data: blob:{extra}; "
        f"connect-src 'self' https://api-adresse.data.gouv.fr{extra}; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self' https://connect.stripe.com"
    )


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    app.wsgi_app = WhiteNoise(
        app.wsgi_app,
        root=os.path.join(os.path.dirname(__file__), "static"),
        prefix="/static/",
        max_age=3600,
        autorefresh=os.getenv("FLASK_DEBUG") == "1",
    )

    if settings.trust_proxy_headers:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.secure_cookies,
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        WTF_CSRF_TIME_LIMIT=None,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        TEMPLATES_AUTO_RELOAD=os.getenv("TEMPLATES_AUTO_RELOAD") == "1",
    )

    csrf.init_app(app)
    limiter.init_app(app)
    install_request_id_hooks(app)

    app.jinja_env.globals.update(
        OrderStatus=OrderStatus,
        PaymentStatus=PaymentStatus,
        QuoteRequestStatus=QuoteRequestStatus,
        QuoteStatus=QuoteStatus,
        QRCStatus=QRCStatus,
        MembershipStatus=MembershipStatus,
        UserRole=UserRole,
        DRINK_LABELS=DRINK_LABELS,
    )

    @app.template_filter("fr_amount")
    def _fr_amount(value, decimals: int = 0) -> str:
        try:
            numeric = float(value or 0)
        except (TypeError, ValueError):
            numeric = 0.0
        formatted = f"{numeric:,.{decimals}f}"
        return formatted.replace(",", " ").replace(".", ",").replace(" ", " ")

    from blueprints.admin import admin_bp
    from blueprints.api import api_bp
    from blueprints.auth import auth_bp
    from blueprints.caterer import caterer_bp
    from blueprints.client import client_bp
    from blueprints.legal import legal_bp
    from blueprints.uploads import uploads_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(client_bp)
    app.register_blueprint(caterer_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(legal_bp)
    app.register_blueprint(uploads_bp)

    limiter.limit("30 per minute", per_method=True, methods=["POST"])(client_bp)
    limiter.limit("30 per minute", per_method=True, methods=["POST"])(caterer_bp)
    limiter.limit("20 per minute", per_method=True, methods=["POST"])(admin_bp)

    demo_mode = os.getenv("ENABLE_DEMO_SEED") == "1"
    if demo_mode:
        from blueprints.devtools import devtools_bp, DEMO_ACCOUNTS

        app.register_blueprint(devtools_bp)
    else:
        DEMO_ACCOUNTS = []

    @app.context_processor
    def _inject_demo_state():
        return {
            "dev_demo_mode": demo_mode,
            "dev_demo_accounts": DEMO_ACCOUNTS,
        }

    @app.context_processor
    def _inject_matomo():
        return {"matomo_tag_manager_url": config.MATOMO_TAG_MANAGER_URL}

    @app.context_processor
    def _inject_notifications():
        from models import Notification as _Notification
        from services.notifications import (
            get_unread_count,
            notification_target_url,
        )

        if not g.get("current_user"):
            return {
                "notifications_recent": [],
                "notifications_unread_total": 0,
                "notification_target_url": notification_target_url,
            }
        db = get_db()
        recent = db.scalars(
            select(_Notification)
            .where(
                _Notification.user_id == g.current_user.id,
                _Notification.is_read.is_(False),
            )
            .order_by(_Notification.created_at.desc())
            .limit(10)
        ).all()
        unread_total = (
            len(recent) if len(recent) < 10 else get_unread_count(db, g.current_user.id)
        )
        return {
            "notifications_recent": recent,
            "notifications_unread_total": unread_total,
            "notification_target_url": notification_target_url,
        }

    from cli import admin_cli, announcements_cli, uploads_cli

    app.cli.add_command(admin_cli)
    app.cli.add_command(uploads_cli)
    app.cli.add_command(announcements_cli)

    @app.before_request
    def load_current_user():
        g.current_user = None
        if request.endpoint and (
            request.endpoint in ("static", "health", "legal.security_txt")
            or (
                request.blueprint == "auth"
                and request.endpoint != "auth.change_password"
            )
        ):
            return
        user_id = session.get("user_id")
        if user_id:
            db = get_db()
            user = db.execute(
                select(User).where(User.id == user_id)
            ).scalar_one_or_none()
            if user and user.membership_status in _BLOCKED_MEMBERSHIP_STATUSES:
                user = None
            if user and not user.is_active:
                session.clear()
                user = None
            if user:
                stamped = session.get("session_epoch")
                live_at = db.scalar(
                    select(User.sessions_invalidated_at).where(User.id == user_id)
                )
                live = live_at.isoformat() if live_at else None
                if stamped != live:
                    session.clear()
                    user = None
            g.current_user = user

    @app.after_request
    def mark_notifications_read_on_entity_view(response):
        if request.method != "GET":
            return response
        if not (200 <= response.status_code < 300):
            return response
        if not g.get("current_user"):
            return response
        endpoint = request.endpoint
        if not endpoint:
            return response

        from services.notifications import (
            mark_read_by_type,
            mark_read_for_entities,
            mark_read_for_entity,
        )

        args = request.view_args or {}
        db = get_db()
        user = g.current_user
        user_id = user.id
        touched = False

        try:
            if endpoint in (
                "client.request_detail",
                "caterer.request_detail",
                "admin.qualification_detail",
            ):
                rid = args.get("request_id") or args.get("qr_id")
                if rid:
                    touched |= bool(
                        mark_read_for_entity(db, user_id, "quote_request", rid)
                    )
                    from models import Quote

                    quote_ids = list(
                        db.scalars(
                            select(Quote.id).where(Quote.quote_request_id == rid)
                        )
                    )
                    if quote_ids:
                        touched |= bool(
                            mark_read_for_entities(db, user_id, "quote", quote_ids)
                        )
            elif endpoint in (
                "client.order_detail",
                "caterer.order_detail",
                "admin.order_detail",
            ):
                oid = args.get("order_id")
                if oid:
                    touched |= bool(mark_read_for_entity(db, user_id, "order", oid))
            elif endpoint == "admin.caterer_detail":
                cid = args.get("caterer_id")
                if cid:
                    touched |= bool(mark_read_for_entity(db, user_id, "caterer", cid))
            elif endpoint in (
                "client.message_thread",
                "caterer.message_thread",
                "admin.message_thread",
            ):
                tid = args.get("thread_id")
                if tid:
                    from models import Message

                    msg_ids = list(
                        db.scalars(select(Message.id).where(Message.thread_id == tid))
                    )
                    if msg_ids:
                        touched |= bool(
                            mark_read_for_entities(db, user_id, "message", msg_ids)
                        )
            elif endpoint == "client.dashboard":
                if user.company_id:
                    touched |= bool(
                        mark_read_for_entity(db, user_id, "company", user.company_id)
                    )
            elif endpoint == "client.team":
                if user.role == UserRole.client_admin:
                    touched |= bool(mark_read_by_type(db, user_id, "user"))

            if touched:
                db.commit()
        except SQLAlchemyError:
            db.rollback()
        return response

    @app.teardown_appcontext
    def remove_session(exc=None):
        ScopedSession.remove()

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = _build_csp(
            config.MATOMO_TAG_MANAGER_URL
        )
        if request.is_secure or settings.secure_cookies:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        if getattr(g, "current_user", None) is not None:
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response

    @app.errorhandler(404)
    def _not_found(_e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def _server_error(_e):
        try:
            ScopedSession.rollback()
        except SQLAlchemyError:
            pass
        return render_template("errors/500.html"), 500

    @app.errorhandler(ValueError)
    def _bad_value(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Donnee invalide."}), 400
        return render_template("errors/400.html"), 400

    @app.errorhandler(429)
    def _rate_limited(_e):
        if request.path.startswith("/api/"):
            return jsonify(
                {"error": "Trop de tentatives. Patientez quelques minutes."}
            ), 429
        return render_template("errors/429.html"), 429

    @app.errorhandler(413)
    def _too_large(_e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Requete trop volumineuse (max 16 Mo)."}), 413
        return render_template("errors/500.html"), 413

    @app.route("/health")
    def health():
        try:
            db = get_db()
            db.execute(text("SELECT 1"))
            return jsonify({"status": "ok", "database": "connected"})
        except SQLAlchemyError:
            return jsonify({"status": "degraded", "database": "disconnected"}), 503

    @app.route("/")
    def landing():
        user = g.get("current_user")
        if user:
            role_dashboards = {
                "client_admin": "client.dashboard",
                "client_user": "client.dashboard",
                "caterer": "caterer.dashboard",
                "super_admin": "admin.dashboard",
            }
            endpoint = role_dashboards.get(user.role, "client.dashboard")
            return redirect(url_for(endpoint))
        return render_template("landing.html")

    return app


if __name__ == "__main__":
    debug_flag = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    create_app().run(debug=debug_flag, port=8000)
