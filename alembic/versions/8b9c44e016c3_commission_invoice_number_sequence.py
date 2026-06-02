from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8b9c44e016c3"
down_revision: Union[str, Sequence[str], None] = "233107e88e63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEQ_NAME = "commission_invoice_number_seq"


def upgrade() -> None:
    op.execute(f"CREATE SEQUENCE IF NOT EXISTS {SEQ_NAME}")
    op.execute(
        f"SELECT setval('{SEQ_NAME}', "
        f"COALESCE((SELECT MAX(invoice_number) FROM commission_invoices), 0) + 1, "
        f"false)"
    )

    op.execute(
        f"ALTER TABLE commission_invoices "
        f"ALTER COLUMN invoice_number SET DEFAULT nextval('{SEQ_NAME}')"
    )

    op.create_unique_constraint(
        "commission_invoices_invoice_number_key",
        "commission_invoices",
        ["invoice_number"],
    )

    op.alter_column(
        "invoices",
        "tva_rate",
        existing_type=sa.NUMERIC(precision=5, scale=4),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "invoices",
        "tva_rate",
        existing_type=sa.NUMERIC(precision=5, scale=4),
        nullable=False,
    )
    op.drop_constraint(
        "commission_invoices_invoice_number_key",
        "commission_invoices",
        type_="unique",
    )
    op.execute(
        "ALTER TABLE commission_invoices ALTER COLUMN invoice_number DROP DEFAULT"
    )
    op.execute(f"DROP SEQUENCE IF EXISTS {SEQ_NAME}")
