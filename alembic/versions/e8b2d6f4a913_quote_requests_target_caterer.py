from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e8b2d6f4a913"
down_revision: Union[str, Sequence[str], None] = "d5f1a8c3b672"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quote_requests",
        sa.Column("target_caterer_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_quote_requests_target_caterer_id",
        "quote_requests",
        "caterers",
        ["target_caterer_id"],
        ["id"],
    )
    op.create_index(
        "ix_quote_requests_target_caterer_id",
        "quote_requests",
        ["target_caterer_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_quote_requests_target_caterer_id", table_name="quote_requests")
    op.drop_constraint(
        "fk_quote_requests_target_caterer_id", "quote_requests", type_="foreignkey"
    )
    op.drop_column("quote_requests", "target_caterer_id")
