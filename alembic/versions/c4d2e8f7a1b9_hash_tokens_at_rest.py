import hashlib
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c4d2e8f7a1b9"
down_revision: Union[str, Sequence[str], None] = "d28a1b4e5f3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()

    pending_resets = bind.execute(
        sa.text(
            "SELECT id, token FROM password_reset_tokens "
            "WHERE used_at IS NULL AND expires_at > NOW()"
        )
    ).fetchall()
    for row_id, raw in pending_resets:
        if len(raw) == 64 and all(c in "0123456789abcdef" for c in raw):
            continue
        bind.execute(
            sa.text("UPDATE password_reset_tokens SET token = :digest WHERE id = :id"),
            {"digest": _sha256_hex(raw), "id": row_id},
        )

    pending_invites = bind.execute(
        sa.text(
            "SELECT id, invite_token FROM company_employees "
            "WHERE invite_token IS NOT NULL AND user_id IS NULL"
        )
    ).fetchall()
    for row_id, raw in pending_invites:
        if len(raw) == 64 and all(c in "0123456789abcdef" for c in raw):
            continue
        bind.execute(
            sa.text(
                "UPDATE company_employees SET invite_token = :digest WHERE id = :id"
            ),
            {"digest": _sha256_hex(raw), "id": row_id},
        )


def downgrade() -> None:
    op.execute(
        "UPDATE password_reset_tokens SET used_at = NOW() "
        "WHERE used_at IS NULL AND expires_at > NOW()"
    )
    op.execute(
        "UPDATE company_employees SET invite_token = NULL, invited_at = NULL "
        "WHERE invite_token IS NOT NULL AND user_id IS NULL"
    )
