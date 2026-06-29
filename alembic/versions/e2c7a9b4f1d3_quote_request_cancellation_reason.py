from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e2c7a9b4f1d3"
down_revision: Union[str, Sequence[str], None] = "d6b9e4c3a2f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Motif d'annulation / rejet d'une demande, stocké séparément du
    # message client (message_to_caterer) pour l'afficher côté client.
    op.add_column(
        "quote_requests",
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quote_requests", "cancellation_reason")
