# Audit C-3 follow-up: read-only check listing demo-seed accounts that
# linger in the DB with the shared `password123` hash. Exit 0 = clean,
# 1 = remediate (rotate or disable).
from __future__ import annotations

import re
import sys
from pathlib import Path

from sqlalchemy import select

from database import session_factory
from models import User

_SEED_FILE = Path(__file__).resolve().parent.parent / "seed_data.py"


def _seeded_emails() -> list[str]:
    # Parse the seeder directly so the canonical list never drifts.
    source = _SEED_FILE.read_text(encoding="utf-8")
    return sorted(set(re.findall(r'email="([^"]+@[^"]+)"', source)))


def main() -> int:
    seeded = _seeded_emails()
    if not seeded:
        sys.stderr.write(
            "Could not extract any email from seed_data.py — pattern drifted?\n"
        )
        return 2

    s = session_factory()
    try:
        rows = s.execute(
            select(User.email, User.is_active, User.role).where(User.email.in_(seeded))
        ).all()
    finally:
        s.close()

    if not rows:
        print(f"OK — none of the {len(seeded)} seeded emails exist in this DB.")
        return 0

    print(
        f"FOUND {len(rows)} seeded account(s) in this DB out of {len(seeded)} possible:"
    )
    for email, is_active, role in rows:
        marker = "active" if is_active else "disabled"
        print(f"  - {email}  ({role}, {marker})")
    print(
        "\nNext step: rotate their password (`flask admin reset-password <email>`) "
        "or disable them (`flask admin disable <email>`)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
