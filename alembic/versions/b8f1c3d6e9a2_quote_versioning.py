from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b8f1c3d6e9a2"
down_revision: Union[str, Sequence[str], None] = "b7a4d2e6c1f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quotes",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "quotes",
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "quotes_supersedes_id_fkey",
        "quotes",
        "quotes",
        ["supersedes_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("quotes_supersedes_id_fkey", "quotes", type_="foreignkey")
    op.drop_column("quotes", "supersedes_id")
    op.drop_column("quotes", "version")
