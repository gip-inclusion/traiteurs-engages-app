from typing import Sequence, Union

from alembic import op


revision: str = "b2f7d4a9c1e3"
down_revision: Union[str, Sequence[str], None] = "a1e3c9f2d7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_payments_stripe_invoice_id",
        "payments",
        ["stripe_invoice_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_payments_stripe_invoice_id", "payments", type_="unique")
