from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a1f3e9c2b4d6"
down_revision: Union[str, Sequence[str], None] = "a92e1c5d4f8b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_changed_at")
