from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c1ab2d3e4f56"
down_revision: Union[str, Sequence[str], None] = "a7b3c1d2e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "caterers",
        sa.Column("service_offerings", sa.JSON(), nullable=True),
    )
    op.add_column(
        "caterers",
        sa.Column("price_per_person_min", sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        "caterers",
        sa.Column("price_per_person_max", sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        "caterers",
        sa.Column("min_advance_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("caterers", "min_advance_days")
    op.drop_column("caterers", "price_per_person_max")
    op.drop_column("caterers", "price_per_person_min")
    op.drop_column("caterers", "service_offerings")
