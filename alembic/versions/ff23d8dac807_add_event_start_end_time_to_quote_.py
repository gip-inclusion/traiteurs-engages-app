from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ff23d8dac807"
down_revision: Union[str, Sequence[str], None] = "11a3c9fbf7ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quote_requests",
        sa.Column("event_start_time", sa.Time(), nullable=True),
    )
    op.add_column(
        "quote_requests",
        sa.Column("event_end_time", sa.Time(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quote_requests", "event_end_time")
    op.drop_column("quote_requests", "event_start_time")
