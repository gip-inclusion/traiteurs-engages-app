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

commission_invoice_seq = Sequence("commission_invoice_number_seq", start=1)


class DietaryMixin:
    dietary_vegetarian: Mapped[bool] = mapped_column(Boolean, default=False)
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

    superseded = "superseded"


class OrderStatus(str, Enum):
    confirmed = "confirmed"
    delivered = "delivered"
    invoicing = "invoicing"
    invoiced = "invoiced"
    paid = "paid"
    disputed = "disputed"


class MealType(str, Enum):
    petit_dejeuner = "petit_dejeuner"
    pause_gourmande = "pause_gourmande"
    plateaux_repas = "plateaux_repas"
    cocktail_dinatoire = "cocktail_dinatoire"
    cocktail_dejeunatoire = "cocktail_dejeunatoire"
    aperitif = "aperitif"


MEAL_TYPE_LABELS: dict[MealType, str] = {
    MealType.petit_dejeuner: "Petit-déjeuner",
    MealType.pause_gourmande: "Pause gourmande",
    MealType.plateaux_repas: "Plateaux repas",
    MealType.cocktail_dinatoire: "Cocktail dînatoire",
    MealType.cocktail_dejeunatoire: "Cocktail déjeunatoire",
    MealType.aperitif: "Apéritif",
}


SERVICE_OFFERING_LABELS: dict[str, str] = {
    m.value: label for m, label in MEAL_TYPE_LABELS.items()
}


DRINK_LABELS: dict[str, str] = {
    "drinks_eau_plate": "Eau plate",
    "drinks_eau_gazeuse": "Eau gazeuse",
    "drinks_soft": "Soft / Jus",
    "drinks_bieres": "Bières",
    "drinks_vins": "Vins",
    "drinks_champagne": "Champagne",
    "drinks_boissons_chaudes": "Boissons chaudes",
}

ALCOHOLIC_DRINKS: frozenset[str] = frozenset(
    {"drinks_bieres", "drinks_vins", "drinks_champagne"}
)


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
    dietary_bio: Mapped[bool] = mapped_column(Boolean, default=False)
    service_config: Mapped[dict | None] = mapped_column(JSON)
    service_offerings: Mapped[list | None] = mapped_column(JSON)
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
    submission_token: Mapped[str | None] = mapped_column(String(36), unique=True)
    service_type: Mapped[str | None] = mapped_column(String(100))
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
    dietary_vegan: Mapped[bool] = mapped_column(Boolean, default=False)
    vegan_count: Mapped[int | None] = mapped_column(Integer)
    halal_count: Mapped[int | None] = mapped_column(Integer)
    gluten_free_count: Mapped[int | None] = mapped_column(Integer)
    lactose_free_count: Mapped[int | None] = mapped_column(Integer)
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

    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotes.id"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    quote_request: Mapped[QuoteRequest] = relationship(back_populates="quotes")
    caterer: Mapped[Caterer] = relationship(back_populates="quotes")
    order: Mapped["Order | None"] = relationship(back_populates="quote")
    supersedes: Mapped["Quote | None"] = relationship(
        remote_side="Quote.id", foreign_keys=[supersedes_id]
    )
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
    __tablename__ = "caterer_reviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    caterer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("caterers.id"), index=True
    )
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
