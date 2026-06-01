from typing import Sequence, Union

from alembic import op
from sqlalchemy import String, bindparam, text


revision: str = "a6f3b8e2d4c7"
down_revision: Union[str, Sequence[str], None] = "c9e8d7a4f1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_QR_FORWARD = {
    "dejeuner": "plateaux_repas",
    "diner": "cocktail_dinatoire",
    "cocktail": "cocktail_dinatoire",
}

_SC_FORWARD = {
    "petit_dejeuner": "petit_dejeuner",
    "dejeuner": "plateaux_repas",
    "diner": "cocktail_dinatoire",
    "cocktail": "cocktail_dinatoire",
    "autre": "plateaux_repas",
}

_NEW_SC_KEYS = (
    "petit_dejeuner",
    "pause_gourmande",
    "plateaux_repas",
    "cocktail_dinatoire",
    "cocktail_dejeunatoire",
    "aperitif",
)


def _remap_service_config(legacy: dict | None) -> dict | None:
    if not isinstance(legacy, dict):
        return None
    out = {k: False for k in _NEW_SC_KEYS}
    for legacy_key, val in legacy.items():
        new_key = _SC_FORWARD.get(legacy_key)
        if new_key is None:
            continue
        out[new_key] = bool(out[new_key] or val)
    return out


def upgrade() -> None:
    conn = op.get_bind()

    op.alter_column(
        "quote_requests",
        "meal_type",
        existing_type=String(length=20),
        type_=String(length=40),
        existing_nullable=True,
    )

    for legacy, new in _QR_FORWARD.items():
        conn.execute(
            text(
                "UPDATE quote_requests SET meal_type = :new WHERE meal_type = :legacy"
            ),
            {"new": new, "legacy": legacy},
        )
    conn.execute(
        text("UPDATE quote_requests SET meal_type = NULL WHERE meal_type = 'autre'")
    )

    rows = conn.execute(
        text("SELECT id, service_config FROM caterers WHERE service_config IS NOT NULL")
    ).fetchall()
    update_stmt = text(
        "UPDATE caterers SET service_config = CAST(:cfg AS JSON) WHERE id = :id"
    ).bindparams(bindparam("cfg"), bindparam("id"))
    import json

    for row in rows:
        new_cfg = _remap_service_config(row.service_config)
        if new_cfg is None:
            continue
        conn.execute(update_stmt, {"cfg": json.dumps(new_cfg), "id": row.id})


def downgrade() -> None:
    conn = op.get_bind()
    reverse = {
        "plateaux_repas": "dejeuner",
        "cocktail_dinatoire": "diner",
        "cocktail_dejeunatoire": "cocktail",
        "aperitif": "cocktail",
        "pause_gourmande": "petit_dejeuner",
    }
    for new, legacy in reverse.items():
        conn.execute(
            text(
                "UPDATE quote_requests SET meal_type = :legacy WHERE meal_type = :new"
            ),
            {"legacy": legacy, "new": new},
        )

    rows = conn.execute(
        text("SELECT id, service_config FROM caterers WHERE service_config IS NOT NULL")
    ).fetchall()
    import json

    update_stmt = text(
        "UPDATE caterers SET service_config = CAST(:cfg AS JSON) WHERE id = :id"
    ).bindparams(bindparam("cfg"), bindparam("id"))
    legacy_keys = ("petit_dejeuner", "dejeuner", "diner", "cocktail", "autre")
    inverse_key = {
        "petit_dejeuner": "petit_dejeuner",
        "pause_gourmande": "petit_dejeuner",
        "plateaux_repas": "dejeuner",
        "cocktail_dinatoire": "diner",
        "cocktail_dejeunatoire": "cocktail",
        "aperitif": "cocktail",
    }
    for row in rows:
        cfg = row.service_config or {}
        if not isinstance(cfg, dict):
            continue
        out = {k: False for k in legacy_keys}
        for k, v in cfg.items():
            lk = inverse_key.get(k)
            if lk is None:
                continue
            out[lk] = bool(out[lk] or v)
        conn.execute(update_stmt, {"cfg": json.dumps(out), "id": row.id})

    op.alter_column(
        "quote_requests",
        "meal_type",
        existing_type=String(length=40),
        type_=String(length=20),
        existing_nullable=True,
    )
