from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f9c8e2b53741"
down_revision: Union[str, Sequence[str], None] = "e7d9c4a1b203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("caterers", "specialties")


def downgrade() -> None:
    op.add_column(
        "caterers",
        sa.Column("specialties", sa.JSON(), nullable=True),
    )
