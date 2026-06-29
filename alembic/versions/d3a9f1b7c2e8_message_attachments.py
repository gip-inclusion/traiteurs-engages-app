"""Message attachment columns.

Ajoute le support d'une pièce jointe (1 par message) dans la messagerie :

* `attachment_url` (clé/URL de stockage, servie par une route
  authentifiée — pas le proxy public /uploads) ;
* `attachment_name` (nom de fichier d'origine, affiché au destinataire).

Les deux sont nullables : un message peut n'avoir aucune pièce jointe.

NB : si la migration `b8f1c3d6e9a2` (versioning de devis, PR #99) est
mergée avant celle-ci, Alembic aura deux têtes partant de
`c4d2e8f7a1b9` → rebaser cette révision sur `b8f1c3d6e9a2` (ou créer
une migration de merge).

Revision ID: d3a9f1b7c2e8
Revises: c4d2e8f7a1b9
Create Date: 2026-05-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d3a9f1b7c2e8"
down_revision: Union[str, Sequence[str], None] = "c4d2e8f7a1b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages", sa.Column("attachment_url", sa.String(500), nullable=True)
    )
    op.add_column(
        "messages", sa.Column("attachment_name", sa.String(255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("messages", "attachment_name")
    op.drop_column("messages", "attachment_url")
