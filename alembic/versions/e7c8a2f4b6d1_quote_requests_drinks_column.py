from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e7c8a2f4b6d1"
down_revision: Union[str, Sequence[str], None] = "f4c8b9a3d7e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quote_requests",
        sa.Column("drinks", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quote_requests", "drinks")
