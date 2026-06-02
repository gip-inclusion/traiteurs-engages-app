from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e7d9c4a1b203"
down_revision: Union[str, Sequence[str], None] = "c1ab2d3e4f56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "caterers",
        sa.Column("service_offering_specs", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("caterers", "service_offering_specs")
