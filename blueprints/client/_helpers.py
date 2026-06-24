import uuid

from sqlalchemy import select

from models import ALCOHOLIC_DRINKS, CompanyService, DRINK_LABELS


ITEMS_PER_PAGE = 12

STATUS_TABS = {
    "all": "Toutes",
    "draft": "Brouillons",
    "pending_review": "En attente",
    "sent_to_caterers": "Envoyees",
    "completed": "Terminees",
}

ORDER_STATUS_LABELS = {
    "confirmed": "Confirmee",
    "delivered": "Livree",
    "invoiced": "Facturee",
    "paid": "Payee",
    "disputed": "Contestee",
}

STRUCTURE_GROUPS = {
    "STPA": ["ESAT", "EA"],
    "SIAE": ["EI", "ACI"],
}

DIETARY_FLAGS = [
    ("vegetarian", "Végétarien"),
    ("halal", "Halal"),
    ("gluten_free", "Sans gluten"),
    ("lactose_free", "Sans lactose"),
]


_QR_DIRECT_FIELDS = (
    "event_date",
    "event_start_time",
    "event_end_time",
    "guest_count",
    "event_latitude",
    "event_longitude",
    "budget_global",
    "budget_per_person",
    "dietary_vegetarian",
    "dietary_halal",
    "dietary_gluten_free",
    "dietary_lactose_free",
    "vegetarian_count",
    "halal_count",
    "gluten_free_count",
    "lactose_free_count",
    "wants_waitstaff",
    "wants_equipment",
    "wants_decoration",
    "wants_nappes",
    "wants_livraison",
    "wants_setup",
    "service_setup_time",
    "wants_cleanup",
    "is_compare_mode",
)

_QR_OPTIONAL_FIELDS = (
    "service_type",
    "meal_type",
    "event_address",
    "event_city",
    "event_zip_code",
    "drinks_details",
    "service_waitstaff_details",
    "service_setup_details",
    "message_to_caterer",
)


def apply_quote_request_form(qr, form):
    for field in _QR_DIRECT_FIELDS:
        setattr(qr, field, getattr(form, field).data)
    for field in _QR_OPTIONAL_FIELDS:
        setattr(qr, field, getattr(form, field).data or None)


def apply_drinks(qr, request_form):
    selected = [
        slug
        for slug in DRINK_LABELS
        if request_form.get(slug, "").strip().lower() in ("1", "true", "on", "yes")
    ]
    qr.drinks = selected or None
    qr.drinks_alcohol = any(slug in ALCOHOLIC_DRINKS for slug in selected)


def own_service_id(db, user, raw):
    if not raw:
        return None
    try:
        candidate = uuid.UUID(raw)
    except (ValueError, TypeError):
        return None
    return db.scalar(
        select(CompanyService.id).where(
            CompanyService.id == candidate,
            CompanyService.company_id == user.company_id,
        )
    )
