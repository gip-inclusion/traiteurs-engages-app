import datetime
import logging
from io import BytesIO

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from sqlalchemy import case, func, select
from sqlalchemy.orm import joinedload, selectinload

from blueprints.middleware import login_required, role_required
from database import get_db
from extensions import limiter
from forms.caterer import RejectionForm
from forms.client import UserProfileForm
from models import (
    MEAL_TYPE_LABELS,
    Caterer,
    Company,
    CompanyEmployee,
    CompanyService,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    Quote,
    QuoteRequest,
    QuoteRequestStatus,
    QuoteStatus,
    User,
    UserRole,
)
from services import messagerie as messagerie_service
from services import workflow
from services.account import apply_profile_form
from services.audit import log_admin_action
from services.notifications import (
    caterer_user_ids,
    company_admin_user_ids,
    notify_users,
)
from services.quotes import build_pdf_preview
from blueprints._notifications import register as _register_notifications

logger = logging.getLogger(__name__)

# Mirror of the caterer/client cap to keep a malicious line-count from
# saturating the WeasyPrint worker.
_MAX_PDF_LINES = 500

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/dashboard")
@login_required
@role_required("super_admin")
def dashboard():
    db = get_db()
    pending_requests = db.scalar(
        select(func.count(QuoteRequest.id)).where(
            QuoteRequest.status == QuoteRequestStatus.pending_review
        )
    )
    pending_caterers = db.scalar(
        select(func.count(Caterer.id)).where(Caterer.is_validated.is_(False))
    )
    active_companies = db.scalar(select(func.count(Company.id)))
    month_start = datetime.date.today().replace(day=1)
    orders_this_month = db.scalar(
        select(func.count(Order.id)).where(Order.created_at >= month_start)
    )
    # File de qualification : uniquement pending_review, en miroir de
    # /admin/qualification.
    recent_requests = (
        db.scalars(
            select(QuoteRequest)
            .options(joinedload(QuoteRequest.company))
            .where(QuoteRequest.status == QuoteRequestStatus.pending_review)
            .order_by(QuoteRequest.created_at.desc())
            .limit(5)
        )
        .unique()
        .all()
    )

    # Eager-load Order → Quote → (QuoteRequest, Caterer) to avoid N+1
    # in the dashboard "à facturer" block.
    orders_to_invoice = (
        db.scalars(
            select(Order)
            .options(
                joinedload(Order.quote)
                .joinedload(Quote.quote_request)
                .joinedload(QuoteRequest.company),
                joinedload(Order.quote).joinedload(Quote.caterer),
            )
            .where(Order.status == OrderStatus.delivered)
            .order_by(Order.updated_at.desc())
            .limit(10)
        )
        .unique()
        .all()
    )

    return render_template(
        "admin/dashboard.html",
        user=g.current_user,
        pending_requests=pending_requests or 0,
        pending_caterers=pending_caterers or 0,
        active_companies=active_companies or 0,
        orders_this_month=orders_this_month or 0,
        recent_requests=recent_requests,
        orders_to_invoice=orders_to_invoice,
        meal_type_labels=MEAL_TYPE_LABELS,
    )


@admin_bp.route("/qualification")
@login_required
@role_required("super_admin")
def qualification():
    db = get_db()
    requests = db.scalars(
        select(QuoteRequest)
        .where(QuoteRequest.status == QuoteRequestStatus.pending_review)
        .order_by(QuoteRequest.created_at.desc())
    ).all()
    return render_template(
        "admin/qualification/list.html",
        user=g.current_user,
        requests=requests,
        meal_type_labels=MEAL_TYPE_LABELS,
    )


_REQUEST_STATUS_TABS: dict[str, str] = {
    "all": "Toutes",
    QuoteRequestStatus.pending_review.value: "En attente",
    QuoteRequestStatus.approved.value: "Approuvées",
    QuoteRequestStatus.sent_to_caterers.value: "Envoyées",
    QuoteRequestStatus.completed.value: "Terminées",
    QuoteRequestStatus.quotes_refused.value: "Devis refusés",
    QuoteRequestStatus.cancelled.value: "Annulées",
    QuoteRequestStatus.draft.value: "Brouillons",
}


_REQUESTS_PAGE_SIZE = 25


@admin_bp.route("/requests")
@login_required
@role_required("super_admin")
def requests_list():
    # Full read-only registry; /qualification is the work-to-do view.
    # SQL paging mirrors /admin/messages (VULN-21) so a 10k-row platform
    # doesn't OOM the worker.
    db = get_db()
    status_filter = request.args.get("status", "all")
    if status_filter not in _REQUEST_STATUS_TABS:
        status_filter = "all"
    page = max(1, request.args.get("page", 1, type=int) or 1)

    # Float pending_review only on the "all" tab; once filtered, the CASE
    # is dead weight.
    stmt = select(QuoteRequest).options(joinedload(QuoteRequest.company))
    count_stmt = select(func.count(QuoteRequest.id))
    if status_filter != "all":
        stmt = stmt.where(QuoteRequest.status == status_filter)
        count_stmt = count_stmt.where(QuoteRequest.status == status_filter)
        stmt = stmt.order_by(QuoteRequest.created_at.desc())
    else:
        pending_priority = case(
            (QuoteRequest.status == QuoteRequestStatus.pending_review, 0),
            else_=1,
        )
        stmt = stmt.order_by(pending_priority, QuoteRequest.created_at.desc())

    total = db.scalar(count_stmt) or 0
    total_pages = max(1, (total + _REQUESTS_PAGE_SIZE - 1) // _REQUESTS_PAGE_SIZE)
    page = min(page, total_pages)
    rows = db.scalars(
        stmt.limit(_REQUESTS_PAGE_SIZE).offset((page - 1) * _REQUESTS_PAGE_SIZE)
    ).all()

    return render_template(
        "admin/requests/list.html",
        user=g.current_user,
        requests=rows,
        status_tabs=_REQUEST_STATUS_TABS,
        current_tab=status_filter,
        meal_type_labels=MEAL_TYPE_LABELS,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@admin_bp.route("/qualification/<uuid:request_id>")
@login_required
@role_required("super_admin")
def qualification_detail(request_id):
    db = get_db()
    qr = db.scalar(
        select(QuoteRequest)
        .where(QuoteRequest.id == request_id)
        .options(
            joinedload(QuoteRequest.user),
            joinedload(QuoteRequest.company),
            selectinload(QuoteRequest.quotes).joinedload(Quote.caterer),
        )
    )
    if not qr:
        abort(404)
    return render_template(
        "admin/qualification/detail.html",
        user=g.current_user,
        qr=qr,
        meal_type_labels=MEAL_TYPE_LABELS,
    )


@admin_bp.route("/qualification/<uuid:request_id>/approve", methods=["POST"])
@login_required
@role_required("super_admin")
def qualification_approve(request_id):
    db = get_db()
    try:
        qrcs = workflow.approve_quote_request(db, request_id=request_id)
    except workflow.RequestNotFound:
        abort(404)
    log_admin_action(
        db,
        g.current_user,
        "quote_request.approve",
        target_type="quote_request",
        target_id=request_id,
        extra={"fanout_size": len(qrcs)},
    )
    db.commit()
    if qrcs:
        flash(f"Demande approuvee et envoyee a {len(qrcs)} traiteur(s).", "success")
    else:
        # Catalogue empty: workflow fans out to all validated caterers, so
        # an empty fanout means no validated caterer exists. Surface so the
        # admin can follow up with the client.
        flash(
            "Demande approuvee, mais aucun traiteur valide n'est present "
            "dans le catalogue. Pensez a contacter le client.",
            "info",
        )
    return redirect(url_for("admin.requests_list"))


@admin_bp.route("/qualification/<uuid:request_id>/reject", methods=["POST"])
@login_required
@role_required("super_admin")
def qualification_reject(request_id):
    form = RejectionForm()
    if not form.validate_on_submit():
        flash("Veuillez corriger les erreurs du formulaire.", "error")
        return redirect(url_for("admin.qualification_detail", request_id=request_id))
    db = get_db()
    try:
        workflow.reject_quote_request(
            db,
            request_id=request_id,
            reason=form.rejection_reason.data,
        )
    except workflow.RequestNotFound:
        abort(404)
    log_admin_action(
        db,
        g.current_user,
        "quote_request.reject",
        target_type="quote_request",
        target_id=request_id,
        extra={"reason": form.rejection_reason.data},
    )
    db.commit()
    flash("Demande rejetee.", "info")
    return redirect(url_for("admin.requests_list"))


@admin_bp.route("/caterers")
@login_required
@role_required("super_admin")
def caterers_list():
    db = get_db()
    caterers = db.scalars(select(Caterer).order_by(Caterer.name)).all()
    return render_template(
        "admin/caterers/list.html", user=g.current_user, caterers=caterers
    )


@admin_bp.route("/caterers/<uuid:caterer_id>")
@login_required
@role_required("super_admin")
def caterer_detail(caterer_id):
    db = get_db()
    caterer = db.get(Caterer, caterer_id)
    if not caterer:
        abort(404)
    return render_template(
        "admin/caterers/detail.html", user=g.current_user, caterer=caterer
    )


@admin_bp.route("/caterers/<uuid:caterer_id>/validate", methods=["POST"])
@login_required
@role_required("super_admin")
def caterer_validate(caterer_id):
    db = get_db()
    caterer = db.get(Caterer, caterer_id)
    if not caterer:
        abort(404)
    caterer.is_validated = True
    log_admin_action(
        db,
        g.current_user,
        "caterer.validate",
        target_type="caterer",
        target_id=caterer_id,
        extra={"caterer_name": caterer.name},
    )
    db.commit()
    flash(f"Traiteur {caterer.name} valide.", "success")
    return redirect(url_for("admin.caterer_detail", caterer_id=caterer_id))


@admin_bp.route("/caterers/<uuid:caterer_id>/invalidate", methods=["POST"])
@login_required
@role_required("super_admin")
def caterer_invalidate(caterer_id):
    db = get_db()
    caterer = db.get(Caterer, caterer_id)
    if not caterer:
        abort(404)
    caterer.is_validated = False
    log_admin_action(
        db,
        g.current_user,
        "caterer.invalidate",
        target_type="caterer",
        target_id=caterer_id,
        extra={"caterer_name": caterer.name},
    )
    db.commit()
    flash(f"Traiteur {caterer.name} invalide.", "info")
    return redirect(url_for("admin.caterer_detail", caterer_id=caterer_id))


@admin_bp.route("/companies")
@login_required
@role_required("super_admin")
def companies_list():
    db = get_db()
    companies = db.scalars(select(Company).order_by(Company.name)).all()
    return render_template(
        "admin/companies/list.html", user=g.current_user, companies=companies
    )


@admin_bp.route("/companies/<uuid:company_id>")
@login_required
@role_required("super_admin")
def company_detail(company_id):
    db = get_db()
    company = db.get(Company, company_id)
    if not company:
        abort(404)
    employees = db.scalars(
        select(CompanyEmployee).where(CompanyEmployee.company_id == company_id)
    ).all()
    services = db.scalars(
        select(CompanyService).where(CompanyService.company_id == company_id)
    ).all()
    requests = db.scalars(
        select(QuoteRequest)
        .where(QuoteRequest.company_id == company_id)
        .order_by(QuoteRequest.created_at.desc())
    ).all()
    return render_template(
        "admin/companies/detail.html",
        user=g.current_user,
        company=company,
        employees=employees,
        services=services,
        requests=requests,
        meal_type_labels=MEAL_TYPE_LABELS,
    )


# ---------------------------------------------------------------------------
# User management — ajouté pour traiter les inscriptions "mauvais rôle"
# (un user qui s'inscrit comme client alors qu'il voulait s'inscrire comme
# traiteur). Hors de ce flux il y a peu d'usages : la liste reste légère
# (pas de pagination tant que < 1k users — on étendra si besoin).
# ---------------------------------------------------------------------------


_USER_ROLE_LABELS = {
    UserRole.client_admin: "Admin client",
    UserRole.client_user: "Utilisateur client",
    UserRole.caterer: "Traiteur",
    UserRole.super_admin: "Super admin",
}


@admin_bp.route("/users")
@login_required
@role_required("super_admin")
def users_list():
    """Liste des utilisateurs avec recherche (?q=) et filtre rôle
    (?role=). Pas de pagination tant que le volume reste raisonnable :
    page rendue côté serveur, scroll OK jusqu'à ~1k users."""
    db = get_db()
    q = (request.args.get("q") or "").strip()
    role_filter = request.args.get("role") or "all"
    stmt = select(User).order_by(User.created_at.desc())
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            (func.lower(User.email).like(like))
            | (func.lower(User.first_name).like(like))
            | (func.lower(User.last_name).like(like))
        )
    if role_filter != "all":
        try:
            stmt = stmt.where(User.role == UserRole(role_filter))
        except ValueError:
            role_filter = "all"
    users = db.scalars(stmt).all()
    return render_template(
        "admin/users/list.html",
        user=g.current_user,
        users=users,
        q=q,
        role_filter=role_filter,
        role_labels=_USER_ROLE_LABELS,
    )


@admin_bp.route("/users/<uuid:user_id>")
@login_required
@role_required("super_admin")
def user_detail(user_id):
    from services.user_admin import (
        can_convert_to_caterer,
        can_delete_user,
        user_metrics,
    )

    db = get_db()
    target = db.get(User, user_id)
    if not target:
        abort(404)
    metrics = user_metrics(db, target)
    delete_blocker = can_delete_user(db, target, actor=g.current_user)
    convert_blocker = can_convert_to_caterer(db, target, actor=g.current_user)
    return render_template(
        "admin/users/detail.html",
        user=g.current_user,
        target=target,
        metrics=metrics,
        delete_blocker=delete_blocker,
        convert_blocker=convert_blocker,
        role_labels=_USER_ROLE_LABELS,
    )


@admin_bp.route("/users/<uuid:user_id>/delete", methods=["POST"])
@login_required
@role_required("super_admin")
def user_delete(user_id):
    from services.user_admin import can_delete_user, delete_user

    db = get_db()
    target = db.get(User, user_id)
    if not target:
        abort(404)
    blocker = can_delete_user(db, target, actor=g.current_user)
    if blocker:
        flash(blocker, "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    target_email = target.email
    log_admin_action(
        db,
        g.current_user,
        "user.delete",
        target_type="user",
        target_id=user_id,
        extra={"email": target_email, "role": str(target.role)},
    )
    delete_user(db, target)
    db.commit()
    flash(f"Compte {target_email} supprime.", "success")
    return redirect(url_for("admin.users_list"))


@admin_bp.route("/users/<uuid:user_id>/convert-to-caterer", methods=["POST"])
@login_required
@role_required("super_admin")
def user_convert_to_caterer(user_id):
    from models import CatererStructureType
    from services.user_admin import can_convert_to_caterer, convert_to_caterer

    db = get_db()
    target = db.get(User, user_id)
    if not target:
        abort(404)
    blocker = can_convert_to_caterer(db, target, actor=g.current_user)
    if blocker:
        flash(blocker, "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    # Inputs requis pour créer la fiche traiteur minimale. La fiche
    # reste invalidée — le traiteur complétera son profil, l'admin
    # validera ensuite via /admin/caterers/<id>/validate.
    caterer_name = (request.form.get("caterer_name") or "").strip()
    caterer_siret = (request.form.get("caterer_siret") or "").strip()
    structure_raw = (request.form.get("structure_type") or "").strip()
    invoice_prefix = (request.form.get("invoice_prefix") or "").strip().upper()

    if not (caterer_name and caterer_siret and structure_raw and invoice_prefix):
        flash(
            "Nom, SIRET, type de structure et prefixe facture sont requis "
            "pour creer la fiche traiteur.",
            "error",
        )
        return redirect(url_for("admin.user_detail", user_id=user_id))
    if len(caterer_siret) != 14 or not caterer_siret.isdigit():
        flash("Le SIRET doit comporter exactement 14 chiffres.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    try:
        structure_type = CatererStructureType(structure_raw)
    except ValueError:
        flash("Type de structure invalide.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    target_email = target.email
    caterer = convert_to_caterer(
        db,
        target,
        caterer_name=caterer_name,
        caterer_siret=caterer_siret,
        structure_type=structure_type,
        invoice_prefix=invoice_prefix,
    )
    log_admin_action(
        db,
        g.current_user,
        "user.convert_to_caterer",
        target_type="user",
        target_id=user_id,
        extra={
            "email": target_email,
            "caterer_id": str(caterer.id),
            "caterer_name": caterer.name,
        },
    )
    db.commit()
    flash(
        f"Compte {target_email} converti en traiteur. Validez la fiche depuis "
        f"« Traiteurs » apres que le compte ait complete son profil.",
        "success",
    )
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/payments")
@login_required
@role_required("super_admin")
def payments():
    status_filter = request.args.get("status", "all")
    db = get_db()
    stmt = select(Payment).order_by(Payment.created_at.desc())
    if status_filter != "all":
        try:
            status_enum = PaymentStatus(status_filter)
        except ValueError:
            status_filter = "all"
        else:
            stmt = stmt.where(Payment.status == status_enum)
    payment_list = db.scalars(stmt).all()

    total_revenue = (
        db.scalar(
            select(func.coalesce(func.sum(Payment.amount_total_cents), 0)).where(
                Payment.status == PaymentStatus.succeeded
            )
        )
        or 0
    )
    total_commission = (
        db.scalar(
            select(func.coalesce(func.sum(Payment.application_fee_cents), 0)).where(
                Payment.status == PaymentStatus.succeeded
            )
        )
        or 0
    )
    pending_count = (
        db.scalar(
            select(func.count(Payment.id)).where(
                Payment.status.in_([PaymentStatus.pending, PaymentStatus.processing])
            )
        )
        or 0
    )

    return render_template(
        "admin/payments.html",
        user=g.current_user,
        payments=payment_list,
        total_revenue=total_revenue,
        total_commission=total_commission,
        pending_count=pending_count,
        current_status=status_filter,
    )


@admin_bp.route("/stats")
@login_required
@role_required("super_admin")
def stats():
    db = get_db()
    today = datetime.date.today()
    months = []
    for i in range(11, -1, -1):
        d = today.replace(day=1) - datetime.timedelta(days=i * 30)
        month_start = d.replace(day=1)
        if month_start.month == 12:
            month_end = month_start.replace(year=month_start.year + 1, month=1)
        else:
            month_end = month_start.replace(month=month_start.month + 1)
        revenue = (
            db.scalar(
                select(func.coalesce(func.sum(Payment.amount_total_cents), 0)).where(
                    Payment.status == PaymentStatus.succeeded,
                    Payment.created_at >= month_start,
                    Payment.created_at < month_end,
                )
            )
            or 0
        )
        months.append(
            {
                "label": month_start.strftime("%b %Y"),
                "revenue": revenue / 100,
            }
        )

    top_caterers_rows = db.execute(
        select(
            Caterer.name,
            func.sum(Payment.amount_total_cents).label("revenue"),
            func.count(Payment.id).label("order_count"),
        )
        .join(Caterer, Payment.caterer_id == Caterer.id)
        .where(Payment.status == PaymentStatus.succeeded)
        .group_by(Caterer.id, Caterer.name)
        .order_by(func.sum(Payment.amount_total_cents).desc())
        .limit(5)
    ).all()
    top_caterers = [
        {
            "name": r.name,
            "revenue": (r.revenue or 0) / 100,
            "order_count": r.order_count,
        }
        for r in top_caterers_rows
    ]

    total_requests = db.scalar(select(func.count(QuoteRequest.id))) or 0
    quotes_sent = (
        db.scalar(select(func.count(Quote.id)).where(Quote.status != QuoteStatus.draft))
        or 0
    )
    quotes_accepted = (
        db.scalar(
            select(func.count(Quote.id)).where(Quote.status == QuoteStatus.accepted)
        )
        or 0
    )
    orders_paid = (
        db.scalar(
            select(func.count(Payment.id)).where(
                Payment.status == PaymentStatus.succeeded
            )
        )
        or 0
    )

    geo_rows = db.execute(
        select(
            QuoteRequest.event_city,
            func.count(QuoteRequest.id).label("cnt"),
        )
        .where(QuoteRequest.event_city.isnot(None))
        .group_by(QuoteRequest.event_city)
        .order_by(func.count(QuoteRequest.id).desc())
        .limit(10)
    ).all()
    geo_data = [{"city": r.event_city, "count": r.cnt} for r in geo_rows]

    meal_rows = db.execute(
        select(
            QuoteRequest.meal_type,
            func.count(QuoteRequest.id).label("cnt"),
        )
        .where(QuoteRequest.meal_type.isnot(None))
        .group_by(QuoteRequest.meal_type)
        .order_by(func.count(QuoteRequest.id).desc())
    ).all()
    meal_slug_to_label = {m.value: label for m, label in MEAL_TYPE_LABELS.items()}
    meal_data = [
        {"type": meal_slug_to_label.get(r.meal_type, r.meal_type), "count": r.cnt}
        for r in meal_rows
    ]


    return render_template(
        "admin/stats.html",
        user=g.current_user,
        months=months,
        top_caterers=top_caterers,
        funnel={
            "requests": total_requests,
            "quotes_sent": quotes_sent,
            "quotes_accepted": quotes_accepted,
            "orders_paid": orders_paid,
        },
        geo_data=geo_data,
        meal_data=meal_data,
    )


ORDER_STATUS_TABS = {
    "all": "Toutes",
    "upcoming": "À venir",
    "delivered": "Livrées",
    "invoiced": "Facturées",
    "paid": "Payées",
    "disputed": "Litige",
}

_TAB_TO_STATUSES = {
    "upcoming": (OrderStatus.confirmed,),
    "delivered": (OrderStatus.delivered,),
    "invoiced": (OrderStatus.invoicing, OrderStatus.invoiced),
    "paid": (OrderStatus.paid,),
    "disputed": (OrderStatus.disputed,),
}

# Manual transitions: each move requires the order's current status to
# match the source.
_ADMIN_ORDER_TRANSITIONS = {
    "invoice": (OrderStatus.delivered, OrderStatus.invoiced),
    "pay": (OrderStatus.invoiced, OrderStatus.paid),
}


@admin_bp.route("/orders")
@login_required
@role_required("super_admin")
def orders_list():
    db = get_db()
    status_filter = request.args.get("status") or "all"
    if status_filter not in ORDER_STATUS_TABS:
        status_filter = "all"

    stmt = (
        select(Order)
        .options(
            joinedload(Order.quote)
            .joinedload(Quote.quote_request)
            .joinedload(QuoteRequest.company),
            joinedload(Order.quote).joinedload(Quote.caterer),
        )
        .order_by(Order.created_at.desc())
    )
    if status_filter != "all":
        stmt = stmt.where(Order.status.in_(_TAB_TO_STATUSES[status_filter]))

    orders = db.scalars(stmt).unique().all()
    return render_template(
        "admin/orders/list.html",
        user=g.current_user,
        orders=orders,
        status_tabs=ORDER_STATUS_TABS,
        current_tab=status_filter,
    )


@admin_bp.route("/orders/<uuid:order_id>")
@login_required
@role_required("super_admin")
def order_detail(order_id):
    db = get_db()
    order = db.scalar(
        select(Order)
        .options(
            joinedload(Order.quote).options(
                selectinload(Quote.lines),
                joinedload(Quote.caterer),
                joinedload(Quote.quote_request).joinedload(QuoteRequest.company),
            ),
            selectinload(Order.payments),
        )
        .where(Order.id == order_id)
    )
    if not order:
        abort(404)
    pdf_preview = (
        build_pdf_preview(order.quote, order.quote.quote_request, order.quote.caterer)
        if order.quote.lines
        else None
    )
    return render_template(
        "admin/orders/detail.html",
        user=g.current_user,
        order=order,
        pdf_preview=pdf_preview,
        meal_type_labels=MEAL_TYPE_LABELS,
    )


@admin_bp.route("/quotes/<uuid:q_id>/pdf", methods=["GET"])
@login_required
@role_required("super_admin")
@limiter.limit("20 per minute")
def quote_pdf(q_id):
    # Lazy import: WeasyPrint loads Cairo/Pango at import time.
    from services.quote_pdf import render_quote_pdf

    db = get_db()
    quote = db.scalar(
        select(Quote)
        .options(
            selectinload(Quote.lines),
            joinedload(Quote.caterer),
            joinedload(Quote.quote_request).options(
                joinedload(QuoteRequest.company),
                joinedload(QuoteRequest.user),
            ),
        )
        .where(Quote.id == q_id)
    )
    if not quote:
        abort(404)
    if len(quote.lines) > _MAX_PDF_LINES:
        abort(413)

    pdf_bytes = render_quote_pdf(quote, quote.quote_request, quote.caterer)
    logger.info(
        "quote_pdf_downloaded admin_user_id=%s quote_id=%s reference=%s lines=%d",
        g.current_user.id,
        quote.id,
        quote.reference,
        len(quote.lines),
    )
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"devis-{quote.reference}.pdf",
    )


@admin_bp.route("/orders/<uuid:order_id>/transition", methods=["POST"])
@login_required
@role_required("super_admin")
def order_transition(order_id):
    action = request.form.get("action")
    if action not in _ADMIN_ORDER_TRANSITIONS:
        abort(400)
    expected, target = _ADMIN_ORDER_TRANSITIONS[action]

    db = get_db()
    order = db.get(Order, order_id)
    if not order:
        abort(404)

    if order.status != expected:
        flash(
            f"Transition impossible : la commande est en statut {order.status.value}.",
            "error",
        )
        return redirect(url_for("admin.order_detail", order_id=order_id))

    previous = (
        OrderStatus(order.status) if isinstance(order.status, str) else order.status
    )
    order.status = target
    log_admin_action(
        db,
        g.current_user,
        f"order.{action}",
        target_type="order",
        target_id=order_id,
        extra={"from": previous.value, "to": target.value},
    )

    qr = order.quote.quote_request
    caterer_id = order.quote.caterer_id
    if action == "invoice":
        client_title = "Facture disponible"
        client_body = "Votre facture est prête. Vous pouvez la consulter depuis le détail de la commande."
        caterer_title = "Commande facturée"
        caterer_body = "La commande a été facturée par notre équipe."
    elif action == "pay":
        client_title = "Paiement reçu"
        client_body = "Le paiement de votre commande a été enregistré. Merci !"
        caterer_title = "Paiement reçu"
        caterer_body = (
            "Le paiement de la commande a été enregistré et sera viré sous peu."
        )
    else:
        client_title = caterer_title = f"Commande passée en {target.value}"
        client_body = caterer_body = ""

    notify_users(
        db,
        company_admin_user_ids(db, qr.company_id),
        type=f"order_{action}",
        title=client_title,
        body=client_body,
        related_entity_type="order",
        related_entity_id=order_id,
    )
    notify_users(
        db,
        caterer_user_ids(db, caterer_id),
        type=f"order_{action}",
        title=caterer_title,
        body=caterer_body,
        related_entity_type="order",
        related_entity_id=order_id,
    )

    # notify_review_invite is idempotent so both admin and webhook paths
    # can call it.
    if target == OrderStatus.paid:
        from services.reviews import notify_review_invite

        notify_review_invite(db, order=order)
    db.commit()
    flash(f"Commande passée en {target.value}.", "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


def _admin_messagerie_ctx(*, threads, active_thread_id, active):
    return {
        "threads": threads,
        "active_thread_id": active_thread_id,
        "active": active,
        "list_endpoint": "admin.messages",
        "thread_endpoint": "admin.message_thread",
        "show_role_badges": True,
        "read_only": False,
        "current_user_id": str(g.current_user.id),
    }


@admin_bp.route("/messages")
@login_required
@role_required("super_admin")
def messages():
    db = get_db()
    threads = messagerie_service.threads_for_viewer(db, g.current_user)
    return render_template(
        "messagerie/page.html",
        user=g.current_user,
        messagerie_ctx=_admin_messagerie_ctx(
            threads=threads,
            active_thread_id=None,
            active=None,
        ),
    )


@admin_bp.route("/messages/<uuid:thread_id>")
@login_required
@role_required("super_admin")
def message_thread(thread_id):
    db = get_db()
    active = messagerie_service.active_thread_context(
        db, thread_id=thread_id, viewer=g.current_user
    )
    if active is None:
        abort(404)
    threads = messagerie_service.threads_for_viewer(db, g.current_user)
    return render_template(
        "messagerie/page.html",
        user=g.current_user,
        messagerie_ctx=_admin_messagerie_ctx(
            threads=threads,
            active_thread_id=str(thread_id),
            active=active,
        ),
    )


@admin_bp.route("/profile", methods=["GET", "POST"])
@login_required
@role_required("super_admin")
def profile():
    user = g.current_user
    if request.method == "POST":
        form = UserProfileForm()
        if not form.validate_on_submit():
            flash("Veuillez corriger les erreurs du formulaire.", "error")
            return render_template("admin/profile.html", user=user), 400
        db = get_db()
        err = apply_profile_form(db, user, form)
        if err:
            flash(err, "error")
            return render_template("admin/profile.html", user=user), 400
        db.commit()
        flash("Profil mis à jour.", "success")
        return redirect(url_for("admin.profile"))
    return render_template("admin/profile.html", user=user)


_register_notifications(admin_bp, roles=("super_admin",))
