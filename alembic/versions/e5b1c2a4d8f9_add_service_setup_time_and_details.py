from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5b1c2a4d8f9"
down_revision: Union[str, Sequence[str], None] = "ff23d8dac807"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quote_requests",
        sa.Column(
            "wants_nappes",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "quote_requests",
        sa.Column(
            "wants_livraison",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "quote_requests",
        sa.Column("service_setup_time", sa.Time(), nullable=True),
    )
    op.add_column(
        "quote_requests",
        sa.Column("service_setup_details", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quote_requests", "service_setup_details")
    op.drop_column("quote_requests", "service_setup_time")
    op.drop_column("quote_requests", "wants_livraison")
    op.drop_column("quote_requests", "wants_nappes")
