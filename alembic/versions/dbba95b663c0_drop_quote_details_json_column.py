from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "dbba95b663c0"
down_revision: Union[str, Sequence[str], None] = "1e6220186827"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("quotes", "details")


def downgrade() -> None:
    op.add_column(
        "quotes",
        sa.Column("details", postgresql.JSON(astext_type=sa.Text()), nullable=True),
    )
