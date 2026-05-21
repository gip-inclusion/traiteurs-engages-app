import datetime

from sqlalchemy import select

from models import TermsVersion


def is_terms_accepted(form) -> bool:
    # Fail-closed: anything other than "on"/"1"/"true" is refusal.
    return (form.get("accept_terms") or "").strip().lower() in ("on", "1", "true")


def current_terms_version(db, today: datetime.date | None = None) -> TermsVersion:
    # Highest effective_at <= today; created_at breaks same-day ties.
    # Raises RuntimeError if nothing matches — that's a deploy mistake.
    if today is None:
        today = datetime.date.today()
    row = db.scalar(
        select(TermsVersion)
        .where(TermsVersion.effective_at <= today)
        .order_by(
            TermsVersion.effective_at.desc(),
            TermsVersion.created_at.desc(),
        )
        .limit(1)
    )
    if row is None:
        raise RuntimeError(
            "No TermsVersion in force today — check the alembic seed migration."
        )
    return row
