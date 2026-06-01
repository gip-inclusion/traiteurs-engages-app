from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_to_none(v):
    if isinstance(v, str) and v == "":
        return None
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    secret_key: SecretStr = Field(min_length=32)
    database_url: str

    @field_validator("database_url", mode="before")
    @classmethod
    def _fix_postgres_scheme(cls, v: str) -> str:
        if isinstance(v, str) and v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql://", 1)
        return v

    redis_url: str | None = None

    stripe_secret_key: SecretStr | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: SecretStr | None = None

    admin_email: str = "admin@traiteurs-engages.fr"
    admin_initial_password: SecretStr | None = None

    s3_bucket: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SCW_S3_BUCKET", "S3_BUCKET"),
    )
    s3_region: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SCW_S3_REGION", "S3_REGION"),
    )
    s3_access_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SCW_ACCESS_KEY", "S3_ACCESS_KEY"),
    )
    s3_secret_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("SCW_SECRET_KEY", "S3_SECRET_KEY"),
    )
    s3_endpoint_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SCW_S3_ENDPOINT_URL", "S3_ENDPOINT_URL"),
    )
    s3_public_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SCW_S3_PUBLIC_URL", "S3_PUBLIC_URL"),
    )

    db_pool_size: int = 10
    db_pool_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    secure_cookies: bool = True
    trust_proxy_headers: bool = False

    billing_enabled: bool = False

    brevo_api_key: SecretStr | None = None
    mail_from_email: str = "noreply@les-traiteurs-engages.fr"
    mail_from_name: str = "Les Traiteurs Engagés"
    base_url: str = "http://localhost:8000"

    support_user_emails: str | None = None

    @field_validator(
        "stripe_secret_key",
        "stripe_publishable_key",
        "stripe_webhook_secret",
        "admin_initial_password",
        "s3_bucket",
        "s3_region",
        "s3_access_key",
        "s3_secret_key",
        "s3_endpoint_url",
        "s3_public_url",
        "brevo_api_key",
        mode="before",
    )
    @classmethod
    def _opt_empty_to_none(cls, v):
        return _empty_to_none(v)

    @field_validator("mail_from_email", "mail_from_name", "base_url", mode="before")
    @classmethod
    def _mail_empty_to_default(cls, v, info):
        defaults = {
            "mail_from_email": "noreply@les-traiteurs-engages.fr",
            "mail_from_name": "Les Traiteurs Engagés",
            "base_url": "http://localhost:8000",
        }
        if isinstance(v, str) and v == "":
            return defaults[info.field_name]
        return v

    @field_validator("admin_email", mode="before")
    @classmethod
    def _email_empty_to_default(cls, v):
        return v if (isinstance(v, str) and v) else "admin@traiteurs-engages.fr"

    @field_validator(
        "secure_cookies", "trust_proxy_headers", "billing_enabled", mode="before"
    )
    @classmethod
    def _bool_empty_to_false(cls, v):
        if isinstance(v, str) and v == "":
            return False
        return v


settings = Settings()


SECRET_KEY = settings.secret_key.get_secret_value()
DATABASE_URL = settings.database_url
STRIPE_SECRET_KEY = (
    settings.stripe_secret_key.get_secret_value() if settings.stripe_secret_key else ""
)
STRIPE_PUBLISHABLE_KEY = settings.stripe_publishable_key or ""
STRIPE_WEBHOOK_SECRET = (
    settings.stripe_webhook_secret.get_secret_value()
    if settings.stripe_webhook_secret
    else ""
)

BREVO_API_KEY = (
    settings.brevo_api_key.get_secret_value() if settings.brevo_api_key else ""
)
MAIL_FROM_EMAIL = settings.mail_from_email
MAIL_FROM_NAME = settings.mail_from_name
BASE_URL = settings.base_url.rstrip("/")
SUPPORT_USER_EMAILS = frozenset(
    e.strip().lower()
    for e in (settings.support_user_emails or "").split(",")
    if e.strip()
)
