from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c5a8f3b2e1d7"
down_revision: Union[str, Sequence[str], None] = "b8f1c3d6e9a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Caterer side : drop dietary_vegan (option retiree de la fiche
    # traiteur), introduit dietary_bio. Cote QuoteRequest (table
    # quote_requests) on conserve dietary_vegan intact : le client peut
    # toujours demander un menu vegan.
    op.add_column(
        "caterers",
        sa.Column(
            "dietary_bio",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("caterers", "dietary_bio", server_default=None)
    op.drop_column("caterers", "dietary_vegan")


def downgrade() -> None:
    op.add_column(
        "caterers",
        sa.Column(
            "dietary_vegan",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("caterers", "dietary_vegan", server_default=None)
    op.drop_column("caterers", "dietary_bio")
