import json
import logging
from io import BytesIO

from flask import (
    abort,
    flash,
    g,
    redirect,
    render_template,
    request as flask_request,
    send_file,
    url_for,
)
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from blueprints.middleware import (
    login_required,
    role_required,
    validated_caterer_required,
)
from blueprints.scoping import get_caterer_qrc, get_caterer_quote
from database import get_db
from extensions import limiter
from forms.caterer import QuoteForm
from models import (
    MEAL_TYPE_LABELS,
    Order,
    QRCStatus,
    Quote,
    QuoteRequest,
    QuoteRequestCaterer,
    QuoteRequestStatus,
    QuoteStatus,
)
from services import workflow
from services.quotes import (
    build_pdf_preview,
    calculate_quote_totals,
    generate_quote_reference,
    lines_from_dicts,
)

logger = logging.getLogger(__name__)

_MAX_PDF_LINES = 500


def _parse_line_dicts(raw: str) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list) or not all(
        isinstance(d, dict)
        and all(isinstance(v, (str, int, float, bool, type(None))) for v in d.values())
        for d in data
    ):
        return []
    return data


def _derive_qrc_display_status(qr, caterer_id):
    qrc = next(
        (link for link in qr.caterers if link.caterer_id == caterer_id),
        None,
    )

    candidate_quotes = [
        q
        for q in qr.quotes
        if q.caterer_id == caterer_id
        and q.status != QuoteStatus.superseded
        and not (q.status == QuoteStatus.draft and q.supersedes_id is not None)
    ]
    caterer_quote = max(candidate_quotes, key=lambda q: q.version, default=None)
    no_active_quote = caterer_quote is None or caterer_quote.status == QuoteStatus.draft
    # Refus explicite par le traiteur : on rejoint l'onglet "Cloturees"
    # quelle que soit la presence d'un devis brouillon. request_reject
    # empeche deja un refus apres envoi d'un devis "sent", donc le seul
    # devis residuel possible est un brouillon non envoye.
    if qrc and qrc.status == QRCStatus.rejected:
        return "closed"
    if qrc and qrc.status == QRCStatus.closed and no_active_quote:
        return "closed"
    if no_active_quote:
        return "new"
    if caterer_quote.status == QuoteStatus.refused:
        return "quotes_refused"
    if caterer_quote.status == QuoteStatus.accepted:
        return "quote_accepted"
    return "sent"


REQUEST_STATUS_TABS = {
    "all": "Toutes",
    "new": "Nouvelles",
    "sent": "Devis envoye",
    "quote_accepted": "Commande creee",
    "quotes_refused": "Devis refuse",
    "closed": "Cloturees",
}


def register(bp):
    @bp.route("/requests")
    @login_required
    @role_required("caterer")
    @validated_caterer_required
    def requests_list():
        caterer = g.current_user.caterer
        status_filter = flask_request.args.get("status") or "all"
        if status_filter not in REQUEST_STATUS_TABS:
            status_filter = "all"
        db = get_db()
        stmt = (
            select(QuoteRequestCaterer)
            .where(QuoteRequestCaterer.caterer_id == caterer.id)
            .order_by(QuoteRequestCaterer.id.desc())
        )
        qrcs = db.scalars(stmt).all()
        for qrc in qrcs:
            qr = qrc.quote_request
            _ = qr.company
            qrc.display_status = _derive_qrc_display_status(qr, caterer.id)
        if status_filter != "all":
            qrcs = [q for q in qrcs if q.display_status == status_filter]
        return render_template(
            "caterer/requests/list.html",
            user=g.current_user,
            qrcs=qrcs,
            status_tabs=REQUEST_STATUS_TABS,
            current_tab=status_filter,
            meal_type_labels=MEAL_TYPE_LABELS,
        )

    @bp.route("/requests/<uuid:qr_id>")
    @login_required
    @role_required("caterer")
    @validated_caterer_required
    def request_detail(qr_id):
        caterer = g.current_user.caterer
        db = get_db()
        qrc = get_caterer_qrc(qr_id, caterer.id)
        qr = qrc.quote_request
        _ = qr.company
        _ = qr.user

        existing_quote = db.scalar(
            select(Quote)
            .where(Quote.quote_request_id == qr_id)
            .where(Quote.caterer_id == caterer.id)
            .where(Quote.status != QuoteStatus.superseded)
            .where(
                (Quote.status != QuoteStatus.draft) | (Quote.supersedes_id.is_(None))
            )
            .order_by(Quote.version.desc())
        )
        qrc.display_status = _derive_qrc_display_status(qr, caterer.id)
        previous_orders = db.scalars(
            select(Order)
            .join(Quote, Order.quote_id == Quote.id)
            .join(QuoteRequest, Quote.quote_request_id == QuoteRequest.id)
            .where(Quote.caterer_id == caterer.id)
            .where(QuoteRequest.company_id == qr.company_id)
            .where(QuoteRequest.id != qr.id)
            .order_by(Order.created_at.desc())
            .limit(5)
        ).all()
        pdf_preview = None
        if existing_quote and existing_quote.lines:
            pdf_preview = build_pdf_preview(existing_quote, qr, caterer)
        return render_template(
            "caterer/requests/detail.html",
            user=g.current_user,
            qr=qr,
            qrc=qrc,
            existing_quote=existing_quote,
            previous_orders=previous_orders,
            meal_type_labels=MEAL_TYPE_LABELS,
            pdf_preview=pdf_preview,
        )

    @bp.route("/requests/<uuid:qr_id>/reject", methods=["POST"])
    @login_required
    @role_required("caterer")
    @validated_caterer_required
    def request_reject(qr_id):
        caterer = g.current_user.caterer
        db = get_db()
        qrc = get_caterer_qrc(qr_id, caterer.id)
        existing_quote = db.scalar(
            select(Quote)
            .where(Quote.quote_request_id == qr_id)
            .where(Quote.caterer_id == caterer.id)
        )
        if existing_quote and existing_quote.status != QuoteStatus.draft:
            flash("Impossible de refuser une demande après envoi du devis.", "error")
            return redirect(url_for("caterer.request_detail", qr_id=qr_id))
        qrc.status = QRCStatus.rejected
        db.commit()
        flash("La demande a été refusée.", "info")
        return redirect(url_for("caterer.requests_list"))

    @bp.route("/requests/<uuid:qr_id>/quote/new", methods=["GET"])
    @login_required
    @role_required("caterer")
    @validated_caterer_required
    def quote_new(qr_id):
        caterer = g.current_user.caterer
        db = get_db()
        qrc = get_caterer_qrc(qr_id, caterer.id)
        qr = qrc.quote_request
        _ = qr.company
        if qr.status != QuoteRequestStatus.sent_to_caterers:
            flash(
                "Cette demande est cloturee : elle n'accepte plus de devis.",
                "info",
            )
            return redirect(url_for("caterer.request_detail", qr_id=qr_id))
        if qrc.status == QRCStatus.closed:
            flash(
                "Cette demande est cloturee : trois autres traiteurs ont "
                "deja envoye leur devis au client.",
                "info",
            )
            return redirect(url_for("caterer.request_detail", qr_id=qr_id))
        preview_reference = generate_quote_reference(db, caterer)
        return render_template(
            "caterer/quotes/editor.html",
            user=g.current_user,
            qr=qr,
            qrc=qrc,
            quote=None,
            initial_lines=[],
            preview_reference=preview_reference,
            meal_type_labels=MEAL_TYPE_LABELS,
        )

    @bp.route("/requests/<uuid:qr_id>/quote", methods=["POST"])
    @login_required
    @role_required("caterer")
    @validated_caterer_required
    def quote_create(qr_id):
        caterer = g.current_user.caterer
        db = get_db()
        qrc = get_caterer_qrc(qr_id, caterer.id)
        qr = qrc.quote_request
        existing_draft = db.scalar(
            select(Quote).where(
                Quote.quote_request_id == qr_id,
                Quote.caterer_id == caterer.id,
                Quote.status == QuoteStatus.draft,
            )
        )
        if existing_draft is not None:
            return redirect(
                url_for("caterer.quote_edit", qr_id=qr_id, q_id=existing_draft.id)
            )
        form = QuoteForm()
        if not form.validate_on_submit():
            flash("Veuillez corriger les erreurs du formulaire.", "error")
            return render_template(
                "caterer/quotes/editor.html",
                user=g.current_user,
                qr=qr,
                qrc=qrc,
                quote=None,
                initial_lines=[],
                preview_reference=generate_quote_reference(db, caterer),
                meal_type_labels=MEAL_TYPE_LABELS,
            ), 400
        line_dicts = _parse_line_dicts(form.details.data)
        try:
            lines = lines_from_dicts(line_dicts)
        except ValueError as exc:
            flash(f"Devis invalide : {exc}", "error")
            return render_template(
                "caterer/quotes/editor.html",
                user=g.current_user,
                qr=qr,
                qrc=qrc,
                quote=None,
                initial_lines=line_dicts,
                preview_reference=generate_quote_reference(db, caterer),
                meal_type_labels=MEAL_TYPE_LABELS,
            ), 400
        totals = calculate_quote_totals(
            line_dicts, qr.guest_count, commission_rate=caterer.commission_rate
        )
        reference = generate_quote_reference(db, caterer)
        quote = Quote(
            quote_request_id=qr_id,
            caterer_id=caterer.id,
            reference=reference,
            total_amount_ht=totals["total_ht"],
            amount_per_person=totals["amount_per_person"],
            notes=form.notes.data or "",
            valid_until=form.valid_until.data,
            status=QuoteStatus.draft,
            lines=lines,
        )
        db.add(quote)
        db.commit()
        action = flask_request.form.get("action", "draft")
        if action == "send":
            try:
                workflow.submit_quote(
                    db,
                    request_id=qr_id,
                    quote_id=quote.id,
                    caterer=caterer,
                )
                db.commit()
            except workflow.QuoteNotFound:
                abort(404)
            except workflow.QuoteRequestClosed:
                db.commit()
                flash(
                    "Devis enregistre en brouillon. La demande a ete cloturee "
                    "avant l'envoi : trois autres traiteurs ont deja repondu.",
                    "info",
                )
                return redirect(url_for("caterer.request_detail", qr_id=qr_id))
            except workflow.QuoteRequestNotOpen:
                db.commit()
                flash(
                    "Devis enregistre en brouillon. La demande n'accepte "
                    "plus de devis : un autre traiteur a deja ete retenu.",
                    "info",
                )
                return redirect(url_for("caterer.request_detail", qr_id=qr_id))
            from services import email_triggers

            email_triggers.quote_received(db, quote=quote, caterer=caterer)
            flash("Devis enregistre et envoye au client.", "success")
        elif action == "draft_and_pdf":
            return redirect(url_for("caterer.quote_pdf", qr_id=qr_id, q_id=quote.id))
        else:
            flash("Devis enregistre en brouillon.", "success")
        return redirect(url_for("caterer.request_detail", qr_id=qr_id))

    @bp.route("/requests/<uuid:qr_id>/quote/<uuid:q_id>/edit", methods=["GET"])
    @login_required
    @role_required("caterer")
    @validated_caterer_required
    def quote_edit(qr_id, q_id):
        caterer = g.current_user.caterer
        quote = get_caterer_quote(qr_id, q_id, caterer.id)
        qr = quote.quote_request
        _ = qr.company
        qrc = get_caterer_qrc(qr_id, caterer.id)
        return render_template(
            "caterer/quotes/editor.html",
            user=g.current_user,
            qr=qr,
            qrc=qrc,
            quote=quote,
            initial_lines=[ln.as_dict() for ln in quote.lines],
            preview_reference=quote.reference,
            meal_type_labels=MEAL_TYPE_LABELS,
        )

    @bp.route("/requests/<uuid:qr_id>/quote/<uuid:q_id>/edit", methods=["POST"])
    @login_required
    @role_required("caterer")
    @validated_caterer_required
    def quote_update(qr_id, q_id):
        caterer = g.current_user.caterer
        db = get_db()
        quote = get_caterer_quote(qr_id, q_id, caterer.id)
        if quote.status != QuoteStatus.draft:
            flash("Ce devis a déjà été envoyé et ne peut plus être modifié.", "error")
            return redirect(url_for("caterer.request_detail", qr_id=qr_id))
        qr = quote.quote_request
        qrc = get_caterer_qrc(qr_id, caterer.id)
        form = QuoteForm()
        if not form.validate_on_submit():
            flash("Veuillez corriger les erreurs du formulaire.", "error")
            return render_template(
                "caterer/quotes/editor.html",
                user=g.current_user,
                qr=qr,
                qrc=qrc,
                quote=quote,
                initial_lines=[ln.as_dict() for ln in quote.lines],
                preview_reference=quote.reference,
                meal_type_labels=MEAL_TYPE_LABELS,
            ), 400
        line_dicts = _parse_line_dicts(form.details.data)
        try:
            new_lines = lines_from_dicts(line_dicts)
        except ValueError as exc:
            flash(f"Devis invalide : {exc}", "error")
            return render_template(
                "caterer/quotes/editor.html",
                user=g.current_user,
                qr=qr,
                qrc=qrc,
                quote=quote,
                initial_lines=line_dicts,
                preview_reference=quote.reference,
                meal_type_labels=MEAL_TYPE_LABELS,
            ), 400
        totals = calculate_quote_totals(
            line_dicts, qr.guest_count, commission_rate=caterer.commission_rate
        )
        quote.lines = new_lines
        quote.total_amount_ht = totals["total_ht"]
        quote.amount_per_person = totals["amount_per_person"]
        quote.notes = form.notes.data or ""
        quote.valid_until = (
            form.valid_until.data if form.valid_until.data else quote.valid_until
        )
        db.commit()
        action = flask_request.form.get("action", "draft")
        if action == "send":
            try:
                workflow.submit_quote(
                    db,
                    request_id=qr_id,
                    quote_id=quote.id,
                    caterer=caterer,
                )
                db.commit()
            except workflow.QuoteNotFound:
                abort(404)
            except workflow.QuoteRequestClosed:
                db.commit()
                flash(
                    "Devis mis a jour en brouillon. La demande a ete cloturee "
                    "avant l'envoi : trois autres traiteurs ont deja repondu.",
                    "info",
                )
                return redirect(url_for("caterer.request_detail", qr_id=qr_id))
            except workflow.QuoteRequestNotOpen:
                db.commit()
                flash(
                    "Devis mis a jour en brouillon. La demande n'accepte "
                    "plus de devis : un autre traiteur a deja ete retenu.",
                    "info",
                )
                return redirect(url_for("caterer.request_detail", qr_id=qr_id))
            except workflow.QuoteNotAvailable:
                db.commit()
                flash(
                    "Revision impossible : le devis initial a ete refuse "
                    "par le client entre temps. Vous ne pouvez plus l'envoyer.",
                    "info",
                )
                return redirect(url_for("caterer.request_detail", qr_id=qr_id))
            from services import email_triggers

            email_triggers.quote_received(db, quote=quote, caterer=caterer)
            flash("Devis mis a jour et envoye au client.", "success")
        elif action == "draft_and_pdf":
            return redirect(url_for("caterer.quote_pdf", qr_id=qr_id, q_id=quote.id))
        else:
            flash("Devis mis a jour.", "success")
        return redirect(url_for("caterer.request_detail", qr_id=qr_id))

    @bp.route("/requests/<uuid:qr_id>/quote/<uuid:q_id>/revise", methods=["POST"])
    @login_required
    @role_required("caterer")
    @validated_caterer_required
    def quote_revise(qr_id, q_id):
        caterer = g.current_user.caterer
        db = get_db()
        try:
            revision = workflow.start_quote_revision(
                db, request_id=qr_id, quote_id=q_id, caterer=caterer
            )
            db.commit()
        except (workflow.QuoteNotFound, workflow.RequestNotFound):
            abort(404)
        except workflow.QuoteRequestNotOpen:
            flash(
                "Cette demande n'accepte plus de devis : un autre traiteur "
                "a deja ete retenu.",
                "info",
            )
            return redirect(url_for("caterer.request_detail", qr_id=qr_id))
        return redirect(url_for("caterer.quote_edit", qr_id=qr_id, q_id=revision.id))

    @bp.route("/requests/<uuid:qr_id>/quote/<uuid:q_id>/pdf", methods=["GET"])
    @login_required
    @role_required("caterer")
    @validated_caterer_required
    @limiter.limit("20 per minute")
    def quote_pdf(qr_id, q_id):
        from services.quote_pdf import render_quote_pdf

        caterer = g.current_user.caterer
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
            .where(Quote.caterer_id == caterer.id)
            .where(Quote.quote_request_id == qr_id)
        )
        if not quote:
            abort(404)
        if len(quote.lines) > _MAX_PDF_LINES:
            abort(413)

        pdf_bytes = render_quote_pdf(quote, quote.quote_request, quote.caterer)
        logger.info(
            "quote_pdf_downloaded caterer_id=%s quote_id=%s reference=%s lines=%d",
            caterer.id,
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

    @bp.route("/requests/<uuid:qr_id>/quote/<uuid:q_id>/send", methods=["POST"])
    @login_required
    @role_required("caterer")
    @validated_caterer_required
    def quote_send(qr_id, q_id):
        caterer = g.current_user.caterer
        db = get_db()
        try:
            quote = workflow.submit_quote(
                db,
                request_id=qr_id,
                quote_id=q_id,
                caterer=caterer,
            )
        except workflow.QuoteNotFound:
            abort(404)
        except workflow.QuoteRequestClosed:
            db.commit()
            flash(
                "La demande a ete cloturee : trois autres traiteurs ont deja "
                "envoye leur devis. Votre devis reste enregistre en brouillon.",
                "info",
            )
            return redirect(url_for("caterer.request_detail", qr_id=qr_id))
        except workflow.QuoteRequestNotOpen:
            db.commit()
            flash(
                "La demande n'accepte plus de devis : un autre traiteur a "
                "deja ete retenu. Votre devis reste enregistre en brouillon.",
                "info",
            )
            return redirect(url_for("caterer.request_detail", qr_id=qr_id))
        db.commit()

        from services import email_triggers

        email_triggers.quote_received(db, quote=quote, caterer=caterer)

        flash("Devis envoye au client.", "success")
        return redirect(url_for("caterer.request_detail", qr_id=qr_id))
