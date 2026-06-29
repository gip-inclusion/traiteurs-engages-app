"""Merge des deux têtes alembic.

`d3a9f1b7c2e8` (pièces jointes messagerie, PR #101) et
`e2c7a9b4f1d3` (motif d'annulation de demande) descendent toutes deux de
`c4d2e8f7a1b9` et formaient deux têtes parallèles. Cette révision les
fusionne pour rétablir une tête unique. Aucun changement de schéma.
"""

from typing import Sequence, Union

revision: str = "f4e5d6c7b8a9"
down_revision: Union[str, Sequence[str], None] = ("e2c7a9b4f1d3", "d3a9f1b7c2e8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
