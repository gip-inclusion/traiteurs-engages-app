from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d6b9e4c3a2f8"
down_revision: Union[str, Sequence[str], None] = "c5a8f3b2e1d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Le client ne peut plus demander un menu vegan : on retire la
    # colonne et son compteur sur les demandes. Les valeurs historiques
    # sont perdues par choix explicite (le concept disparait du produit).
    op.drop_column("quote_requests", "vegan_count")
    op.drop_column("quote_requests", "dietary_vegan")


def downgrade() -> None:
    op.add_column(
        "quote_requests",
        sa.Column(
            "dietary_vegan",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("quote_requests", "dietary_vegan", server_default=None)
    op.add_column(
        "quote_requests",
        sa.Column("vegan_count", sa.Integer(), nullable=True),
    )
