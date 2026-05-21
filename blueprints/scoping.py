# Every query for a resource owned by a client company or caterer MUST go
# through these helpers so the ownership filter is never forgotten.
#   client_admin → sees every demand/commande of the company
#   client_user  → sees only the demands they created (and their orders)
from flask import abort
from sqlalchemy import select

from database import get_db
from models import (
    CompanyEmployee,
    CompanyService,
    Order,
    Quote,
    QuoteRequest,
    QuoteRequestCaterer,
    User,
    UserRole,
)


def own_requests_filter(user):
    # Returns None for non-client_user so callers can skip the filter
    # without branching.
    if user.role == UserRole.client_user:
        return QuoteRequest.user_id == user.id
    return None


def get_company_request(request_id, user, *, for_update: bool = False):
    # for_update=True acquires a row-level lock — use on status-gated
    # mutations to close the read-then-write race against admin actions.
    db = get_db()
    stmt = select(QuoteRequest).where(
        QuoteRequest.id == request_id,
        QuoteRequest.company_id == user.company_id,
    )
    own_only = own_requests_filter(user)
    if own_only is not None:
        stmt = stmt.where(own_only)
    if for_update:
        stmt = stmt.with_for_update()
    qr = db.execute(stmt).scalar_one_or_none()
    if not qr:
        abort(404)
    return qr


def get_company_order(order_id, user, *, options=None):
    # Scoped via the underlying QuoteRequest so client_user only sees
    # orders flowing from their own demands.
    db = get_db()
    stmt = (
        select(Order)
        .join(Quote, Order.quote_id == Quote.id)
        .join(QuoteRequest, Quote.quote_request_id == QuoteRequest.id)
        .where(Order.id == order_id, QuoteRequest.company_id == user.company_id)
    )
    if options:
        stmt = stmt.options(*options)
    own_only = own_requests_filter(user)
    if own_only is not None:
        stmt = stmt.where(own_only)
    order = db.execute(stmt).scalar_one_or_none()
    if not order:
        abort(404)
    return order


def get_company_service(service_id, company_id):
    db = get_db()
    service = db.scalar(
        select(CompanyService).where(
            CompanyService.id == service_id,
            CompanyService.company_id == company_id,
        )
    )
    if not service:
        abort(404)
    return service


def get_company_employee(employee_id, company_id):
    db = get_db()
    employee = db.scalar(
        select(CompanyEmployee).where(
            CompanyEmployee.id == employee_id,
            CompanyEmployee.company_id == company_id,
        )
    )
    if not employee:
        abort(404)
    return employee


def get_pending_user(user_id, company_id):
    from models import MembershipStatus

    db = get_db()
    user = db.scalar(
        select(User).where(
            User.id == user_id,
            User.company_id == company_id,
            User.membership_status == MembershipStatus.pending,
        )
    )
    if not user:
        abort(404)
    return user


def get_caterer_qrc(qr_id, caterer_id):
    db = get_db()
    qrc = db.scalar(
        select(QuoteRequestCaterer)
        .where(QuoteRequestCaterer.quote_request_id == qr_id)
        .where(QuoteRequestCaterer.caterer_id == caterer_id)
    )
    if not qrc:
        abort(404)
    return qrc


def get_caterer_quote(qr_id, quote_id, caterer_id):
    db = get_db()
    quote = db.scalar(
        select(Quote)
        .where(Quote.id == quote_id)
        .where(Quote.caterer_id == caterer_id)
        .where(Quote.quote_request_id == qr_id)
    )
    if not quote:
        abort(404)
    return quote


def get_caterer_order(order_id, caterer_id, *, options=None):
    db = get_db()
    stmt = (
        select(Order)
        .join(Quote, Order.quote_id == Quote.id)
        .where(Order.id == order_id)
        .where(Quote.caterer_id == caterer_id)
    )
    if options:
        stmt = stmt.options(*options)
    order = db.scalar(stmt)
    if not order:
        abort(404)
    return order
