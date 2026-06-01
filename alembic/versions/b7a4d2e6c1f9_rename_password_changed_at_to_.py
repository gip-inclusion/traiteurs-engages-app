"""rename users.password_changed_at to users.sessions_invalidated_at

Revision ID: b7a4d2e6c1f9
Revises: c4d2e8f7a1b9
Create Date: 2026-06-01
"""

from typing import Sequence, Union

from alembic import op


revision: str = "b7a4d2e6c1f9"
down_revision: Union[str, Sequence[str], None] = "c4d2e8f7a1b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "password_changed_at",
        new_column_name="sessions_invalidated_at",
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "sessions_invalidated_at",
        new_column_name="password_changed_at",
    )
