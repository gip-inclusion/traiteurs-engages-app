import logging
import math
import uuid
from io import BytesIO

from flask import (
    abort,
    flash,
    g,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from blueprints.client._helpers import (
    DIETARY_FLAGS,
    ITEMS_PER_PAGE,
    STRUCTURE_GROUPS,
    apply_drinks,
    apply_quote_request_form,
    own_service_id,
)
from blueprints.middleware import login_required, role_required
from blueprints.scoping import get_company_request, own_requests_filter
from database import get_db
from extensions import limiter
from forms.client import QuoteAcceptForm, QuoteRefuseForm, QuoteRequestForm
from models import (
    MEAL_TYPE_LABELS,
    PRICE_BAND_BOUNDS,
    SERVICE_OFFERING_LABELS,
    Caterer,
    CompanyService,
    Notification,
    QRCStatus,
    Quote,
    QuoteRequest,
    QuoteRequestCaterer,
    QuoteRequestStatus,
    QuoteStatus,
)
from services import workflow
from services.notifications import (
    caterer_user_ids,
    notify_users,
    super_admin_user_ids,
)
from services.quotes import calculate_quote_totals

logger = logging.getLogger(__name__)


def _no_store(response):
    # Evicts the wizard response from bfcache so "back + resubmit" doesn't
    # replay a stale form with checkboxes whose state was DOM-only.
    response.headers["Cache-Control"] = "no-store"
    return response


# Default icon: `utensils`.
_MEAL_TYPE_ICONS: dict[str, str] = {
    "petit_dejeuner": "coffee",
    "pause_gourmande": "cookie",
    "plateaux_repas": "utensils",
    "cocktail_dinatoire": "wine",
    "cocktail_dejeunatoire": "wine",
    "aperitif": "martini",
}


def _resolve_target_caterer(db, raw_id):
    # None means "treat as open demand" — only validated caterers can be
    # targeted (matches the workflow gate).
    raw = (raw_id or "").strip()
    if not raw:
        return None
    try:
        cid = uuid.UUID(raw)
    except ValueError:
        return None
    return db.scalar(
        select(Caterer).where(Caterer.id == cid).where(Caterer.is_validated.is_(True))
    )


def _caterer_capabilities(caterer):
    # Wizard step-4 uses this to grey out régimes the caterer can't honor;
    # None means open demand (no restriction).
    if caterer is None:
        return None
    return {
        "dietary": {
            "vegetarian": bool(caterer.dietary_vegetarian),
            "vegan": bool(caterer.dietary_vegan),
            "halal": bool(caterer.dietary_halal),
            "gluten_free": bool(caterer.dietary_gluten_free),
            "lactose_free": bool(caterer.dietary_lactose_free),
        },
    }


def _meal_type_options(restrict_to: set[str] | None = None) -> list[dict]:
    options = [
        {
            "slug": m.value,
            "label": label,
            "icon": _MEAL_TYPE_ICONS.get(m.value, "utensils"),
        }
        for m, label in MEAL_TYPE_LABELS.items()
    ]
    if restrict_to is None:
        return options
    return [opt for opt in options if opt["slug"] in restrict_to]


REQUEST_STATUS_TABS = {
    "all": "Toutes",
    "awaiting_quotes": "En attente de devis",
    "quotes_received": "Devis reçu(s)",
    "completed": "Commande créée",
    "closed": "Clôturées",
}

# Drafts excluded — the client can't see them.
_QUOTE_RECEIVED_STATUSES = (
    QuoteStatus.sent,
    QuoteStatus.accepted,
    QuoteStatus.refused,
    QuoteStatus.expired,
)

# Mirrors the caterer/requests.py cap; stops a long line list from
# saturating the WeasyPrint worker.
_MAX_PDF_LINES = 500


def _derive_request_display_status(qr):
    """Collapse QR.status + quote presence into a single client-facing code.

    Returns one of: 'awaiting_quotes', 'quotes_received', 'completed',
    'closed' (or 'cancelled', kept distinct so the row badge can stay
    meaningful even though the "Clôturées" tab buckets them together).

    The status_badge component already handles each of these strings
    with a label and a colour, so the template can pass the result
    straight through.
    """
    if qr.status == QuoteRequestStatus.completed:
        return "completed"
    if qr.status == QuoteRequestStatus.cancelled:
        return "cancelled"
    if qr.status == QuoteRequestStatus.quotes_refused:
        return "closed"
    has_received = any(q.status in _QUOTE_RECEIVED_STATUSES for q in qr.quotes)
    return "quotes_received" if has_received else "awaiting_quotes"


def _request_quote_counts(qr):
    # received excludes drafts; expected is 1 (targeted) or 3 (compare mode).
    received = sum(1 for q in qr.quotes if q.status in _QUOTE_RECEIVED_STATUSES)
    expected = 1 if not qr.is_compare_mode else 3
    return received, expected


def register(bp):
    @bp.route("/requests")
    @login_required
    @role_required("client_admin", "client_user")
    def requests_list():
        user = g.current_user
        status_filter = request.args.get("status", "all")
        if status_filter not in REQUEST_STATUS_TABS:
            status_filter = "all"
        search_q = (request.args.get("q") or "").strip().lower()

        db = get_db()
        stmt = (
            select(QuoteRequest)
            .where(QuoteRequest.company_id == user.company_id)
            .options(selectinload(QuoteRequest.quotes))
            .order_by(QuoteRequest.created_at.desc())
        )
        own_only = own_requests_filter(user)
        if own_only is not None:
            stmt = stmt.where(own_only)
        requests = db.execute(stmt).scalars().all()

        for qr in requests:
            qr.display_status = _derive_request_display_status(qr)
            qr.received_quotes, qr.expected_quotes = _request_quote_counts(qr)

        # "closed" buckets cancelled + closed for the user; rows keep their badge.
        if status_filter == "closed":
            requests = [
                r for r in requests if r.display_status in ("closed", "cancelled")
            ]
        elif status_filter != "all":
            requests = [r for r in requests if r.display_status == status_filter]

        # Python-side search — per-company volume is small (< 100 demands).
        if search_q:

            def _matches(qr):
                haystack = " ".join(
                    filter(
                        None,
                        [
                            MEAL_TYPE_LABELS.get(qr.meal_type, "")
                            if qr.meal_type
                            else "",
                            qr.service_type or "",
                            qr.event_city or "",
                            qr.message_to_caterer or "",
                        ],
                    )
                ).lower()
                return search_q in haystack

            requests = [r for r in requests if _matches(r)]

        return render_template(
            "client/requests/list.html",
            user=user,
            requests=requests,
            current_tab=status_filter,
            tabs=REQUEST_STATUS_TABS,
            search_q=search_q,
            meal_type_labels=MEAL_TYPE_LABELS,
        )

    @bp.route("/requests/new", methods=["GET"])
    @login_required
    @role_required("client_admin", "client_user")
    def requests_new():
        user = g.current_user
        db = get_db()
        services = (
            db.execute(
                select(CompanyService).where(
                    CompanyService.company_id == user.company_id
                )
            )
            .scalars()
            .all()
        )
        # ?caterer_id=... ships the demand straight to one caterer and
        # bypasses admin qualification fan-out.
        target_caterer = _resolve_target_caterer(db, request.args.get("caterer_id"))

        # Targeted demand → filter step-1 radios to the caterer's published
        # service_offerings; open demand → full MEAL_TYPE_LABELS list.
        restrict = (
            set(target_caterer.service_offerings or [])
            if target_caterer is not None
            else None
        )
        return _no_store(
            make_response(
                render_template(
                    "client/requests/new.html",
                    user=user,
                    services=services,
                    target_caterer=target_caterer,
                    meal_type_options=_meal_type_options(restrict_to=restrict),
                    caterer_capabilities=_caterer_capabilities(target_caterer),
                    form_token=str(uuid.uuid4()),
                )
            )
        )

    @bp.route("/requests/new", methods=["POST"])
    @login_required
    @role_required("client_admin", "client_user")
    def requests_new_post():
        user = g.current_user
        db = get_db()
        form = QuoteRequestForm()

        # Idempotency token from the GET: a back+resubmit short-circuits to
        # the existing detail page. Scoped to company_id against cross-tenant clash.
        form_token = (request.form.get("form_token") or "").strip()
        if form_token:
            try:
                uuid.UUID(form_token)
            except ValueError:
                form_token = ""
        if form_token:
            existing = db.scalar(
                select(QuoteRequest).where(
                    QuoteRequest.submission_token == form_token,
                    QuoteRequest.company_id == user.company_id,
                )
            )
            if existing is not None:
                flash("Cette demande a deja ete envoyee.", "info")
                return redirect(
                    url_for("client.request_detail", request_id=existing.id)
                )

        # Resolve before validate_on_submit so validate_meal_type can gate
        # a tampered POST that names an offering the caterer doesn't publish.
        target_caterer = _resolve_target_caterer(db, form.target_caterer_id.data)
        if target_caterer is not None:
            form.target_offerings = set(target_caterer.service_offerings or [])

        if not form.validate_on_submit():
            flash("Veuillez corriger les erreurs du formulaire.", "error")
            services = (
                db.execute(
                    select(CompanyService).where(
                        CompanyService.company_id == user.company_id
                    )
                )
                .scalars()
                .all()
            )
            restrict = (
                set(target_caterer.service_offerings or [])
                if target_caterer is not None
                else None
            )
            response = _no_store(
                make_response(
                    render_template(
                        "client/requests/new.html",
                        user=user,
                        services=services,
                        target_caterer=target_caterer,
                        meal_type_options=_meal_type_options(restrict_to=restrict),
                        caterer_capabilities=_caterer_capabilities(target_caterer),
                        form_token=form_token or str(uuid.uuid4()),
                    )
                )
            )
            response.status_code = 400
            return response

        service_id = own_service_id(db, user, form.company_service_id.data)

        if target_caterer is not None:
            # Targeted demand: skip admin review, QRC starts at 'selected'.
            status = QuoteRequestStatus.sent_to_caterers
            is_compare = False
        else:
            # 3-devis flow: admin curates, status sits in pending_review.
            is_compare = bool(form.is_compare_mode.data)
            status = (
                QuoteRequestStatus.pending_review
                if is_compare
                else QuoteRequestStatus.sent_to_caterers
            )

        qr = QuoteRequest(
            company_id=user.company_id,
            user_id=user.id,
            company_service_id=service_id,
            status=status,
            submission_token=form_token or None,
        )
        apply_quote_request_form(qr, form)
        apply_drinks(qr, request.form)
        # Override what apply_quote_request_form wrote — the resolved flow wins.
        qr.is_compare_mode = is_compare
        db.add(qr)
        try:
            db.flush()
        except IntegrityError:
            # Race: a concurrent POST grabbed the same token first.
            db.rollback()
            existing = db.scalar(
                select(QuoteRequest).where(
                    QuoteRequest.submission_token == form_token,
                    QuoteRequest.company_id == user.company_id,
                )
            )
            if existing is None:
                raise
            flash("Cette demande a deja ete envoyee.", "info")
            return redirect(url_for("client.request_detail", request_id=existing.id))

        if target_caterer is not None:
            db.add(
                QuoteRequestCaterer(
                    quote_request_id=qr.id,
                    caterer_id=target_caterer.id,
                    status=QRCStatus.selected,
                )
            )
            # The 3-devis flow notifies via workflow.approve_quote_request;
            # targeted demands skip that path, so notify the caterer here.
            notify_users(
                db,
                caterer_user_ids(db, target_caterer.id),
                type="quote_request_received",
                title="Nouvelle demande de devis",
                body=f"Une demande pour {qr.guest_count or '?'} convives "
                f"({qr.event_city or 'lieu non renseigné'}) vous a été transmise.",
                related_entity_type="quote_request",
                related_entity_id=qr.id,
            )
        elif qr.status == QuoteRequestStatus.pending_review:
            # Demande arrive en file de qualification — alerter les super_admins.
            notify_users(
                db,
                super_admin_user_ids(db),
                type="quote_request_to_qualify",
                title="Nouvelle demande à qualifier",
                body=f"Une demande de {qr.guest_count or '?'} convives à "
                f"{qr.event_city or 'lieu non renseigné'} attend qualification.",
                related_entity_type="quote_request",
                related_entity_id=qr.id,
            )

        qr_id = qr.id
        db.commit()

        flash("Votre demande de devis a ete envoyee avec succes.", "success")
        return redirect(url_for("client.request_detail", request_id=qr_id))

    @bp.route("/requests/<uuid:request_id>")
    @login_required
    @role_required("client_admin", "client_user")
    def request_detail(request_id):
        user = g.current_user
        db = get_db()
        qr = get_company_request(request_id, user)

        # Order by id so qrcs[0] is deterministic (no created_at column).
        qrcs = (
            db.execute(
                select(QuoteRequestCaterer)
                .where(QuoteRequestCaterer.quote_request_id == request_id)
                .order_by(QuoteRequestCaterer.id)
            )
            .scalars()
            .all()
        )

        # Allow-list (not `!= draft`) so a future status doesn't leak by
        # default — drafts are caterer-only WIP and leak pricing if shown.
        quotes = (
            db.execute(
                select(Quote)
                .where(Quote.quote_request_id == request_id)
                .where(Quote.status.in_(_QUOTE_RECEIVED_STATUSES))
                .order_by(Quote.created_at.asc())
            )
            .scalars()
            .all()
        )

        # Mirror the list page so header and rows share the same badge.
        qr.display_status = _derive_request_display_status(qr)

        # Pre-resolve `caterer.users[0]` once per quote/qrc so the template
        # doesn't repeat it across button + modal loops.
        for quote in quotes:
            quote.caterer_user = quote.caterer.users[0] if quote.caterer.users else None
        for qrc in qrcs:
            qrc.caterer_user = qrc.caterer.users[0] if qrc.caterer.users else None

        # pdf_preview stays None for empty quotes (modal opener checks).
        for quote in quotes:
            if quote.lines:
                line_dicts = [ln.as_dict() for ln in quote.lines]
                totals = calculate_quote_totals(
                    line_dicts,
                    qr.guest_count,
                    commission_rate=quote.caterer.commission_rate,
                )
                lines_by_section: dict[str, list] = {}
                for ln in quote.lines:
                    lines_by_section.setdefault(ln.section, []).append(ln)
                quote.pdf_preview = {
                    "lines_by_section": lines_by_section,
                    "totals": totals,
                }
            else:
                quote.pdf_preview = None

        admin_messages = (
            db.execute(
                select(Notification)
                .where(
                    Notification.user_id == user.id,
                    Notification.type == "admin_message",
                    Notification.related_entity_type == "quote_request",
                    Notification.related_entity_id == qr.id,
                )
                .order_by(Notification.created_at.desc())
            )
            .scalars()
            .all()
        )

        return render_template(
            "client/requests/detail.html",
            user=user,
            qr=qr,
            qrcs=qrcs,
            quotes=quotes,
            meal_type_labels=MEAL_TYPE_LABELS,
            admin_messages=admin_messages,
        )

    @bp.route("/requests/<uuid:request_id>/accept-quote", methods=["POST"])
    @limiter.limit("10 per minute")
    @login_required
    # Any company member can accept; cross-company access is blocked by
    # the company_id scope inside workflow.accept_quote.
    @role_required("client_admin", "client_user")
    def accept_quote(request_id):
        form = QuoteAcceptForm()
        if not form.validate_on_submit():
            abort(400)
        try:
            quote_uuid = uuid.UUID(form.quote_id.data)
        except ValueError:
            abort(400)

        db = get_db()
        try:
            order = workflow.accept_quote(
                db,
                request_id=request_id,
                quote_id=quote_uuid,
                user=g.current_user,
            )
        except workflow.RequestNotFound:
            abort(404)
        except workflow.QuoteNotAvailable:
            flash("Ce devis n'est plus disponible.", "error")
            return redirect(url_for("client.request_detail", request_id=request_id))
        except workflow.QuoteExpired:
            flash("Ce devis a expire.", "error")
            return redirect(url_for("client.request_detail", request_id=request_id))
        db.commit()

        from services import email_triggers

        email_triggers.order_confirmed(db, order=order)

        flash("Devis accepte ! La commande a ete creee.", "success")
        return redirect(url_for("client.order_detail", order_id=order.id))

    @bp.route("/requests/<uuid:request_id>/refuse-quote", methods=["POST"])
    @login_required
    @role_required("client_admin", "client_user")
    def refuse_quote(request_id):
        form = QuoteRefuseForm()
        if not form.validate_on_submit():
            abort(400)
        try:
            quote_uuid = uuid.UUID(form.quote_id.data)
        except ValueError:
            abort(400)

        db = get_db()
        try:
            workflow.refuse_quote(
                db,
                request_id=request_id,
                quote_id=quote_uuid,
                user=g.current_user,
                reason=form.refusal_reason.data or None,
            )
        except (workflow.RequestNotFound, workflow.QuoteNotFound):
            abort(404)
        db.commit()

        flash("Devis refuse.", "info")
        return redirect(url_for("client.request_detail", request_id=request_id))

    @bp.route("/requests/<uuid:request_id>/quote/<uuid:q_id>/pdf", methods=["GET"])
    @login_required
    @role_required("client_admin", "client_user")
    @limiter.limit("20 per minute")
    def quote_pdf(request_id, q_id):
        # Lazy: WeasyPrint pulls Cairo/Pango at import.
        from services.quote_pdf import render_quote_pdf

        user = g.current_user
        db = get_db()
        # get_company_request applies company_id + own_requests_filter
        # so a client_user can't fetch a colleague's QR.
        qr = get_company_request(request_id, user)
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
            .where(Quote.quote_request_id == qr.id)
            # Allow-list (drafts are caterer-only WIP).
            .where(Quote.status.in_(_QUOTE_RECEIVED_STATUSES))
        )
        if not quote:
            abort(404)
        if len(quote.lines) > _MAX_PDF_LINES:
            abort(413)

        pdf_bytes = render_quote_pdf(quote, quote.quote_request, quote.caterer)
        logger.info(
            "quote_pdf_downloaded company_id=%s user_id=%s quote_id=%s reference=%s lines=%d",
            user.company_id,
            user.id,
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

    @bp.route("/requests/<uuid:request_id>/edit", methods=["GET"])
    @login_required
    @role_required("client_admin", "client_user")
    def request_edit(request_id):
        user = g.current_user
        db = get_db()
        qr = get_company_request(request_id, user)
        if qr.status not in (
            QuoteRequestStatus.draft,
            QuoteRequestStatus.pending_review,
        ):
            flash("Cette demande ne peut plus etre modifiee.", "error")
            return redirect(url_for("client.request_detail", request_id=request_id))

        services = (
            db.execute(
                select(CompanyService).where(
                    CompanyService.company_id == user.company_id
                )
            )
            .scalars()
            .all()
        )

        return _no_store(
            make_response(
                render_template(
                    "client/requests/edit.html",
                    user=user,
                    qr=qr,
                    services=services,
                    meal_type_options=_meal_type_options(),
                )
            )
        )

    @bp.route("/requests/<uuid:request_id>/edit", methods=["POST"])
    @login_required
    @role_required("client_admin", "client_user")
    def request_edit_post(request_id):
        user = g.current_user

        db = get_db()
        # Row-level lock against an admin Approve racing this edit and
        # fanning out QRCs on the OLD payload.
        qr = get_company_request(request_id, user, for_update=True)
        if qr.status not in (
            QuoteRequestStatus.draft,
            QuoteRequestStatus.pending_review,
        ):
            flash("Cette demande ne peut plus etre modifiee.", "error")
            return redirect(url_for("client.request_detail", request_id=request_id))

        form = QuoteRequestForm()
        if not form.validate_on_submit():
            flash("Veuillez corriger les erreurs du formulaire.", "error")
            services = (
                db.execute(
                    select(CompanyService).where(
                        CompanyService.company_id == user.company_id
                    )
                )
                .scalars()
                .all()
            )
            response = _no_store(
                make_response(
                    render_template(
                        "client/requests/edit.html",
                        user=user,
                        qr=qr,
                        services=services,
                        meal_type_options=_meal_type_options(),
                    )
                )
            )
            response.status_code = 400
            return response

        qr.company_service_id = own_service_id(db, user, form.company_service_id.data)
        apply_quote_request_form(qr, form)
        apply_drinks(qr, request.form)

        # VULN-36: editing a request that is already awaiting admin qualification
        # MUST keep it in pending_review.
        if qr.status == QuoteRequestStatus.pending_review:
            qr.status = QuoteRequestStatus.pending_review
        else:
            qr.status = (
                QuoteRequestStatus.pending_review
                if form.is_compare_mode.data
                else QuoteRequestStatus.sent_to_caterers
            )

        db.commit()

        flash("Demande mise a jour.", "success")
        return redirect(url_for("client.request_detail", request_id=request_id))

    @bp.route("/search")
    @limiter.limit("60 per minute")
    def search():
        # Public catalogue (no @login_required). Per-IP rate limit because
        # the per-blueprint default only catches POSTs — 60/min throttles
        # bulk scrapers without slowing paginated browsing.
        page = request.args.get("page", 1, type=int)
        q = request.args.get("q", "").strip()
        location = request.args.get("location", "").strip()
        # Legacy single-value `structure_type` kept for query-string compat.
        structure_groups = request.args.getlist("structure_type_multi")
        structure_type = request.args.get("structure_type", "")
        dietary = request.args.getlist("dietary")
        capacity = request.args.get("capacity", type=int)
        service_offerings = request.args.getlist("service_offering")
        budget_bands = [
            b for b in request.args.getlist("budget_range") if b in PRICE_BAND_BOUNDS
        ]
        service_type = request.args.get("service_type", "")

        db = get_db()
        stmt = select(Caterer).where(Caterer.is_validated.is_(True))

        struct_codes: set[str] = set()
        for group in structure_groups:
            if group in STRUCTURE_GROUPS:
                struct_codes.update(STRUCTURE_GROUPS[group])
            elif group:
                struct_codes.add(group)
        if structure_type:
            if structure_type in STRUCTURE_GROUPS:
                struct_codes.update(STRUCTURE_GROUPS[structure_type])
            else:
                struct_codes.add(structure_type)
        if struct_codes:
            stmt = stmt.where(Caterer.structure_type.in_(struct_codes))

        for flag in dietary:
            col = getattr(Caterer, f"dietary_{flag}", None)
            if col is not None:
                stmt = stmt.where(col.is_(True))

        if capacity:
            stmt = stmt.where(
                or_(Caterer.capacity_max.is_(None), Caterer.capacity_max >= capacity)
            )

        if location:
            # Loose match: typing "75" finds Paris, "Paris" finds the city.
            loc_pattern = f"%{location}%"
            stmt = stmt.where(
                or_(
                    Caterer.city.ilike(loc_pattern),
                    Caterer.zip_code.ilike(loc_pattern),
                )
            )

        # JSON-array-contains via LIKE on the encoded text stays portable
        # across Postgres and SQLite. Quote the slug so "petit_dejeuner"
        # doesn't match a hypothetical "petit_dejeuner_xyz".
        for slug in service_offerings:
            if slug in SERVICE_OFFERING_LABELS:
                stmt = stmt.where(
                    cast(Caterer.service_offerings, String).ilike(f'%"{slug}"%')
                )

        # Per-band OR — caterer [min, max] must overlap the band's bounds.
        if budget_bands:
            band_clauses = []
            for band in budget_bands:
                band_min, band_max = PRICE_BAND_BOUNDS[band]
                clauses = []
                if band_min is not None:
                    clauses.append(
                        or_(
                            Caterer.price_per_person_max.is_(None),
                            Caterer.price_per_person_max >= band_min,
                        )
                    )
                if band_max is not None:
                    clauses.append(
                        or_(
                            Caterer.price_per_person_min.is_(None),
                            Caterer.price_per_person_min <= band_max,
                        )
                    )
                if clauses:
                    band_clauses.append(and_(*clauses))
            if band_clauses:
                stmt = stmt.where(or_(*band_clauses))

        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Caterer.name.ilike(pattern),
                    Caterer.description.ilike(pattern),
                )
            )

        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        total_pages = max(1, math.ceil(total / ITEMS_PER_PAGE))
        page = max(1, min(page, total_pages))

        caterers = db.scalars(
            stmt.order_by(Caterer.name)
            .offset((page - 1) * ITEMS_PER_PAGE)
            .limit(ITEMS_PER_PAGE)
        ).all()

        # Single-query hydration for the row badges ("★ 4.3 · 12 avis").
        from services.reviews import (
            ReviewAggregate,
            aggregates_for_caterers,
        )

        review_aggregates = aggregates_for_caterers(db, [c.id for c in caterers])
        for c in caterers:
            agg = review_aggregates.get(c.id, ReviewAggregate(avg=None, count=0))
            c.review_avg = agg.avg
            c.review_count = agg.count

        return render_template(
            "client/search.html",
            user=g.current_user,
            caterers=caterers,
            page=page,
            total_pages=total_pages,
            total=total,
            q=q,
            location=location,
            structure_groups=structure_groups,
            structure_type=structure_type,
            dietary=dietary,
            capacity=capacity,
            service_type=service_type,
            service_offerings=service_offerings,
            budget_bands=budget_bands,
            dietary_flags=DIETARY_FLAGS,
            meal_type_labels=MEAL_TYPE_LABELS,
            service_offering_labels=SERVICE_OFFERING_LABELS,
        )

    @bp.route("/caterers/<uuid:caterer_id>")
    @limiter.limit("60 per minute")
    def caterer_detail(caterer_id):
        # Public fiche (no @login_required). Per-IP rate limit because
        # the per-blueprint default only catches POSTs.
        db = get_db()
        caterer = db.get(Caterer, caterer_id)
        if not caterer or not caterer.is_validated:
            abort(404)
        from services.reviews import (
            aggregate_for_caterer,
            format_author,
            list_for_caterer,
        )

        review_aggregate = aggregate_for_caterer(db, caterer.id)
        reviews = list_for_caterer(db, caterer.id, limit=50)
        return render_template(
            "client/caterer_detail.html",
            user=g.current_user,
            caterer=caterer,
            dietary_flags=DIETARY_FLAGS,
            meal_type_labels=MEAL_TYPE_LABELS,
            service_offering_labels=SERVICE_OFFERING_LABELS,
            review_aggregate=review_aggregate,
            reviews=reviews,
            review_format_author=format_author,
        )
