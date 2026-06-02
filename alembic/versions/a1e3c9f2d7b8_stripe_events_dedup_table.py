from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a1e3c9f2d7b8"
down_revision: Union[str, Sequence[str], None] = "1e6220186827"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("stripe_events")
