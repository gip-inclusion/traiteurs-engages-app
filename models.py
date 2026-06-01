import datetime
import uuid
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    Sequence,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# French tax law requires strictly sequential, no-gap commission invoice
# numbers — Postgres SEQUENCE owns the counter.
commission_invoice_seq = Sequence("commission_invoice_number_seq", start=1)


class DietaryMixin:
    dietary_vegetarian: Mapped[bool] = mapped_column(Boolean, default=False)
    dietary_vegan: Mapped[bool] = mapped_column(Boolean, default=False)
    dietary_halal: Mapped[bool] = mapped_column(Boolean, default=False)
    dietary_gluten_free: Mapped[bool] = mapped_column(Boolean, default=False)
    dietary_lactose_free: Mapped[bool] = mapped_column(Boolean, default=False)


class Base(DeclarativeBase):
    pass


class UserRole(str, Enum):
    client_admin = "client_admin"
    client_user = "client_user"
    caterer = "caterer"
    super_admin = "super_admin"


class MembershipStatus(str, Enum):
    pending = "pending"
    active = "active"
    rejected = "rejected"


class CatererStructureType(str, Enum):
    ESAT = "ESAT"
    EA = "EA"
    EI = "EI"
    ACI = "ACI"


class QuoteRequestStatus(str, Enum):
    draft = "draft"
    pending_review = "pending_review"
    approved = "approved"
    sent_to_caterers = "sent_to_caterers"
    completed = "completed"
    cancelled = "cancelled"
    quotes_refused = "quotes_refused"


class QRCStatus(str, Enum):
    selected = "selected"
    responded = "responded"
    transmitted_to_client = "transmitted_to_client"
    rejected = "rejected"
    closed = "closed"


class QuoteStatus(str, Enum):
    draft = "draft"
    sent = "sent"
    accepted = "accepted"
    refused = "refused"
    expired = "expired"


class OrderStatus(str, Enum):
    confirmed = "confirmed"
    delivered = "delivered"
    # `invoicing` = dramatiq job enqueued; promoted to `invoiced` on success,
    # left here on failure so the retry CLI can pick it up (P3.4).
    invoicing = "invoicing"
    invoiced = "invoiced"
    paid = "paid"
    disputed = "disputed"


class MealType(str, Enum):
    # Must stay aligned with the caterer's "Catalogue & tarifs" slugs so
    # the client filter and caterer publication describe the same thing.
    petit_dejeuner = "petit_dejeuner"
    pause_gourmande = "pause_gourmande"
    plateaux_repas = "plateaux_repas"
    cocktail_dinatoire = "cocktail_dinatoire"
    cocktail_dejeunatoire = "cocktail_dejeunatoire"
    aperitif = "aperitif"


# Order defines the rendering order of radios/checkboxes everywhere the
# prestation list appears (wizard, caterer profile, catalog filter).
MEAL_TYPE_LABELS: dict[MealType, str] = {
    MealType.petit_dejeuner: "Petit-déjeuner",
    MealType.pause_gourmande: "Pause gourmande",
    MealType.plateaux_repas: "Plateaux repas",
    MealType.cocktail_dinatoire: "Cocktail dînatoire",
    MealType.cocktail_dejeunatoire: "Cocktail déjeunatoire",
    MealType.aperitif: "Apéritif",
}


# Slug→label view of MEAL_TYPE_LABELS for call sites that handle the slug
# as a string (e.g. Caterer.service_offerings JSON column).
SERVICE_OFFERING_LABELS: dict[str, str] = {
    m.value: label for m, label in MEAL_TYPE_LABELS.items()
}


# Slug also doubles as the wizard checkbox `name` (e.g. drinks_eau_plate)
# and as an entry in QuoteRequest.drinks when ticked.
DRINK_LABELS: dict[str, str] = {
    "drinks_eau_plate": "Eau plate",
    "drinks_eau_gazeuse": "Eau gazeuse",
    "drinks_soft": "Soft / Jus",
    "drinks_bieres": "Bières",
    "drinks_vins": "Vins",
    "drinks_champagne": "Champagne",
    "drinks_boissons_chaudes": "Boissons chaudes",
}

# Drives the derived `drinks_alcohol` flag — any new alcoholic entry added
# to DRINK_LABELS MUST be mirrored here or the flag will silently miss it.
ALCOHOLIC_DRINKS: frozenset[str] = frozenset(
    {"drinks_bieres", "drinks_vins", "drinks_champagne"}
)


# Per-person price bands in EUR. A caterer matches when its range overlaps
# with [min, max].
PRICE_BAND_BOUNDS: dict[str, tuple[Decimal | None, Decimal | None]] = {
    "lt15": (None, Decimal("15")),
    "15_30": (Decimal("15"), Decimal("30")),
    "30_50": (Decimal("30"), Decimal("50")),
    "gt50": (Decimal("50"), None),
}


class PaymentStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    succeeded = "succeeded"
    failed = "failed"
    refunded = "refunded"
    canceled = "canceled"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    siret: Mapped[str] = mapped_column(String(14), unique=True)
    address: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(255))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    logo_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    users: Mapped[list["User"]] = relationship(back_populates="company")
    services: Mapped[list["CompanyService"]] = relationship(back_populates="company")
    employees: Mapped[list["CompanyEmployee"]] = relationship(back_populates="company")
    quote_requests: Mapped[list["QuoteRequest"]] = relationship(
        back_populates="company"
    )


class Caterer(DietaryMixin, Base):
    __tablename__ = "caterers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    siret: Mapped[str] = mapped_column(String(14))
    structure_type: Mapped[CatererStructureType] = mapped_column(String(10))
    address: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(255))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    photos: Mapped[list | None] = mapped_column(JSON)
    capacity_min: Mapped[int | None] = mapped_column(Integer)
    capacity_max: Mapped[int | None] = mapped_column(Integer)
    is_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    commission_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=Decimal("0.05")
    )
    logo_url: Mapped[str | None] = mapped_column(String(500))
    delivery_radius_km: Mapped[int | None] = mapped_column(Integer)
    service_config: Mapped[dict | None] = mapped_column(JSON)
    service_offerings: Mapped[list | None] = mapped_column(JSON)
    # {slug: {capacity_min, capacity_max, price_per_person_min, total_min,
    # min_advance_days}}. Global capacity/price/advance columns are derived
    # from this dict on save and read by matching/search.
    service_offering_specs: Mapped[dict | None] = mapped_column(JSON)
    price_per_person_min: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    price_per_person_max: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    min_advance_days: Mapped[int | None] = mapped_column(Integer)
    stripe_account_id: Mapped[str | None] = mapped_column(String(255))
    stripe_onboarded_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    stripe_charges_enabled: Mapped[bool | None] = mapped_column(Boolean)
    stripe_payouts_enabled: Mapped[bool | None] = mapped_column(Boolean)
    invoice_prefix: Mapped[str | None] = mapped_column(String(10), unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    users: Mapped[list["User"]] = relationship(back_populates="caterer")
    quote_request_caterers: Mapped[list["QuoteRequestCaterer"]] = relationship(
        back_populates="caterer"
    )
    quotes: Mapped[list["Quote"]] = relationship(back_populates="caterer")
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="caterer")
    payments: Mapped[list["Payment"]] = relationship(back_populates="caterer")


class TermsVersion(Base):
    # CGS text itself lives in templates/legal/cgs_<slug>.html so the
    # version history stays in git; this row carries only the metadata
    # Flask needs at runtime.
    __tablename__ = "terms_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(50), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    template_name: Mapped[str] = mapped_column(String(255))
    effective_at: Mapped[datetime.date] = mapped_column(Date)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(255))
    last_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(String(20))
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("companies.id"), index=True
    )
    caterer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("caterers.id"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    membership_status: Mapped[MembershipStatus | None] = mapped_column(String(20))
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255))
    sessions_invalidated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    # Nullable: pre-CGS-gate users stay untouched; new signups must fill both.
    terms_accepted_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("terms_versions.id")
    )
    terms_accepted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    company: Mapped[Company | None] = relationship(back_populates="users")
    caterer: Mapped[Caterer | None] = relationship(back_populates="users")
    terms_accepted_version: Mapped["TermsVersion | None"] = relationship()
    quote_requests: Mapped[list["QuoteRequest"]] = relationship(back_populates="user")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="user")
    sent_messages: Mapped[list["Message"]] = relationship(
        foreign_keys="Message.sender_id", back_populates="sender"
    )
    received_messages: Mapped[list["Message"]] = relationship(
        foreign_keys="Message.recipient_id", back_populates="recipient"
    )


class CompanyService(Base):
    __tablename__ = "company_services"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    annual_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    company: Mapped[Company] = relationship(back_populates="services")
    employees: Mapped[list["CompanyEmployee"]] = relationship(back_populates="service")
    quote_requests: Mapped[list["QuoteRequest"]] = relationship(
        back_populates="company_service"
    )


class CompanyEmployee(Base):
    __tablename__ = "company_employees"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id"), index=True
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("company_services.id"), index=True
    )
    first_name: Mapped[str] = mapped_column(String(255))
    last_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    position: Mapped[str | None] = mapped_column(String(255))
    invited_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    # Single-use signup token generated via /client/team, redeemed on
    # /signup/invite/<token>; expires INVITE_TOKEN_TTL_DAYS after invited_at.
    invite_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))

    company: Mapped[Company] = relationship(back_populates="employees")
    service: Mapped[CompanyService | None] = relationship(back_populates="employees")
    user: Mapped[User | None] = relationship()


class QuoteRequest(DietaryMixin, Base):
    __tablename__ = "quote_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    company_service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("company_services.id"), index=True
    )
    status: Mapped[QuoteRequestStatus] = mapped_column(
        String(30), default=QuoteRequestStatus.draft
    )
    # Idempotency token: UNIQUE so a double-submit lands on the existing row
    # (POST handler catches IntegrityError and redirects to the original).
    submission_token: Mapped[str | None] = mapped_column(String(36), unique=True)
    service_type: Mapped[str | None] = mapped_column(String(100))
    # 40 chars fits the longest slug `cocktail_dejeunatoire` (21) plus
    # headroom for future offerings without another migration.
    meal_type: Mapped[MealType | None] = mapped_column(String(40))
    event_date: Mapped[datetime.date | None] = mapped_column(Date)
    event_start_time: Mapped[datetime.time | None] = mapped_column(Time)
    event_end_time: Mapped[datetime.time | None] = mapped_column(Time)
    guest_count: Mapped[int | None] = mapped_column(Integer)
    event_address: Mapped[str | None] = mapped_column(String(500))
    event_city: Mapped[str | None] = mapped_column(String(255))
    event_zip_code: Mapped[str | None] = mapped_column(String(10))
    event_latitude: Mapped[float | None] = mapped_column(Float)
    event_longitude: Mapped[float | None] = mapped_column(Float)
    budget_global: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    budget_per_person: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    vegetarian_count: Mapped[int | None] = mapped_column(Integer)
    vegan_count: Mapped[int | None] = mapped_column(Integer)
    halal_count: Mapped[int | None] = mapped_column(Integer)
    gluten_free_count: Mapped[int | None] = mapped_column(Integer)
    lactose_free_count: Mapped[int | None] = mapped_column(Integer)
    # `drinks_alcohol` is a derived shortcut kept for legacy callers; the
    # canonical state of what the client ticked is `drinks` (JSON list of
    # DRINK_LABELS slugs, fed by the 7 step-5 wizard checkboxes).
    drinks_alcohol: Mapped[bool] = mapped_column(Boolean, default=False)
    drinks: Mapped[list | None] = mapped_column(JSON)
    drinks_details: Mapped[str | None] = mapped_column(Text)
    wants_waitstaff: Mapped[bool] = mapped_column(Boolean, default=False)
    service_waitstaff_details: Mapped[str | None] = mapped_column(Text)
    wants_equipment: Mapped[bool] = mapped_column(Boolean, default=False)
    wants_decoration: Mapped[bool] = mapped_column(Boolean, default=False)
    wants_nappes: Mapped[bool] = mapped_column(Boolean, default=False)
    wants_livraison: Mapped[bool] = mapped_column(Boolean, default=False)
    wants_setup: Mapped[bool] = mapped_column(Boolean, default=False)
    # service_setup_time est obligatoire côté UI quand wants_setup est coché,
    # mais nullable en DB pour ne pas casser les anciennes demandes.
    service_setup_time: Mapped[datetime.time | None] = mapped_column(Time)
    service_setup_details: Mapped[str | None] = mapped_column(Text)
    wants_cleanup: Mapped[bool] = mapped_column(Boolean, default=False)
    is_compare_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    message_to_caterer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    company: Mapped[Company] = relationship(back_populates="quote_requests")
    user: Mapped[User] = relationship(back_populates="quote_requests")
    company_service: Mapped[CompanyService | None] = relationship(
        back_populates="quote_requests"
    )
    caterers: Mapped[list["QuoteRequestCaterer"]] = relationship(
        back_populates="quote_request"
    )
    quotes: Mapped[list["Quote"]] = relationship(back_populates="quote_request")
    messages: Mapped[list["Message"]] = relationship(back_populates="quote_request")


class QuoteRequestCaterer(Base):
    __tablename__ = "quote_request_caterers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    quote_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quote_requests.id"), index=True
    )
    caterer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("caterers.id"), index=True
    )
    status: Mapped[QRCStatus] = mapped_column(String(30), default=QRCStatus.selected)
    responded_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    response_rank: Mapped[int | None] = mapped_column(Integer)

    quote_request: Mapped[QuoteRequest] = relationship(back_populates="caterers")
    caterer: Mapped[Caterer] = relationship(back_populates="quote_request_caterers")


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    quote_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quote_requests.id"), index=True
    )
    caterer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("caterers.id"), index=True
    )
    reference: Mapped[str] = mapped_column(String(50), unique=True)
    total_amount_ht: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    amount_per_person: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    valid_until: Mapped[datetime.date | None] = mapped_column(Date)
    status: Mapped[QuoteStatus] = mapped_column(String(20), default=QuoteStatus.draft)
    refusal_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    quote_request: Mapped[QuoteRequest] = relationship(back_populates="quotes")
    caterer: Mapped[Caterer] = relationship(back_populates="quotes")
    order: Mapped["Order | None"] = relationship(back_populates="quote")
    lines: Mapped[list["QuoteLine"]] = relationship(
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="QuoteLine.position",
    )


class QuoteLine(Base):
    __tablename__ = "quote_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotes.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(50), default="principal")
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    unit_price_ht: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    tva_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("10"))

    quote: Mapped[Quote] = relationship(back_populates="lines")

    def as_dict(self) -> dict:
        return {
            "section": self.section,
            "description": self.description or "",
            "quantity": float(self.quantity),
            "unit_price_ht": float(self.unit_price_ht),
            "tva_rate": float(self.tva_rate),
        }


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotes.id"), unique=True
    )
    client_admin_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    status: Mapped[OrderStatus] = mapped_column(
        String(20), default=OrderStatus.confirmed
    )
    delivery_date: Mapped[datetime.date | None] = mapped_column(Date)
    delivery_address: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    stripe_invoice_id: Mapped[str | None] = mapped_column(String(255))
    stripe_hosted_invoice_url: Mapped[str | None] = mapped_column(String(500))
    invoice_attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    quote: Mapped[Quote] = relationship(back_populates="order")
    client_admin: Mapped[User] = relationship()
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="order")
    commission_invoices: Mapped[list["CommissionInvoice"]] = relationship(
        back_populates="order"
    )
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")
    messages: Mapped[list["Message"]] = relationship(back_populates="order")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("orders.id"), index=True
    )
    caterer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("caterers.id"), index=True
    )
    reference: Mapped[str | None] = mapped_column(String(50))
    amount_ht: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    tva_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    amount_ttc: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    esat_mention: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    order: Mapped[Order] = relationship(back_populates="invoices")
    caterer: Mapped[Caterer] = relationship(back_populates="invoices")


class CommissionInvoice(Base):
    __tablename__ = "commission_invoices"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # French fiscal compliance: numbering owned by Postgres sequence so
    # callers never set invoice_number explicitly.
    invoice_number: Mapped[int] = mapped_column(
        Integer,
        commission_invoice_seq,
        server_default=commission_invoice_seq.next_value(),
        unique=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"))
    party: Mapped[str] = mapped_column(String(20))
    amount_ht: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    tva_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.20"))
    amount_ttc: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    order: Mapped[Order] = relationship(back_populates="commission_invoices")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("orders.id"), index=True
    )
    caterer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("caterers.id"), index=True
    )
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255))
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255))
    # Audit #6: UNIQUE so a race on POST /caterer/orders/<id>/deliver can't
    # create duplicate Payment rows that the webhook only updates one of.
    stripe_invoice_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    stripe_charge_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[PaymentStatus] = mapped_column(
        String(20), default=PaymentStatus.pending
    )
    amount_total_cents: Mapped[int | None] = mapped_column(Integer)
    application_fee_cents: Mapped[int | None] = mapped_column(Integer)
    amount_to_caterer_cents: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    order: Mapped[Order] = relationship(back_populates="payments")
    caterer: Mapped[Caterer] = relationship(back_populates="payments")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    related_entity_type: Mapped[str | None] = mapped_column(String(50))
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="notifications")


class AuditLog(Base):
    # Append-only by convention (no UPDATE/DELETE from app code). actor_email
    # is snapshotted alongside actor_id so deleting a user doesn't erase the
    # audit trail. Written by services.audit.log_admin_action().
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    extra: Mapped[dict | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


class StripeEvent(Base):
    # Audit #3: dedup Stripe webhook events. PK = Stripe's evt_..., so
    # inserting inside the handler gives atomic dedup against replays
    # within the 300s signature window and re-deliveries.
    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    sender_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("orders.id"))
    quote_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quote_requests.id")
    )
    body: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    sender: Mapped[User] = relationship(
        foreign_keys=[sender_id], back_populates="sent_messages"
    )
    recipient: Mapped[User] = relationship(
        foreign_keys=[recipient_id], back_populates="received_messages"
    )
    order: Mapped[Order | None] = relationship(back_populates="messages")
    quote_request: Mapped[QuoteRequest | None] = relationship(back_populates="messages")


class PasswordResetToken(Base):
    # One-shot: verifier refuses tokens with used_at set or expires_at < now.
    # Never edited/extended; old rows kept for audit and for telling users
    # with a stale bookmarked link that the link is dead.
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    user: Mapped[User] = relationship()


class CatererReview(Base):
    # One review per Order (UNIQUE on order_id) and rating ∈ [1, 5] enforced
    # in the DB; the reviewer/status gate runs in services.reviews. Public
    # author display reduced via services.reviews.format_author.
    __tablename__ = "caterer_reviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    caterer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("caterers.id"), index=True
    )
    # Uniqueness is declared via the named UniqueConstraint in __table_args__
    # below; adding unique=True here would create a second unnamed index.
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"))
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True
    )
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    caterer: Mapped["Caterer"] = relationship()
    order: Mapped["Order"] = relationship()
    reviewer: Mapped["User"] = relationship()

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="caterer_reviews_rating_range"),
        UniqueConstraint("order_id", name="caterer_reviews_order_unique"),
    )
