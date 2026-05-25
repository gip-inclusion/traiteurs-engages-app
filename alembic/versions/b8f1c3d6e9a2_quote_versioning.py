"""Quote versioning columns for revised quotes.

Ajoute le support des devis révisés (« DEVIS-…-V2 ») :

* `version` (int, défaut 1) — numéro de version du devis. 1 = devis
  initial, 2+ = révisions successives.
* `supersedes_id` (FK → quotes.id, nullable) — pointe sur le devis
  directement remplacé. Permet d'afficher « annule et remplace le
  devis n°… du … » et de marquer l'ancien `superseded`.

Les rows existantes prennent `version = 1` via le server_default ;
`supersedes_id` reste NULL (aucun devis n'est encore une révision).

Revision ID: b8f1c3d6e9a2
Revises: c4d2e8f7a1b9
Create Date: 2026-05-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b8f1c3d6e9a2"
down_revision: Union[str, Sequence[str], None] = "c4d2e8f7a1b9"
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
