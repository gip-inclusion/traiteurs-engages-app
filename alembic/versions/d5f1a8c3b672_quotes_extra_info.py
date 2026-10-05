from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d5f1a8c3b672"
down_revision: Union[str, Sequence[str], None] = "c7e1a4b9d035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("quotes", sa.Column("extra_info", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("quotes", "extra_info")
