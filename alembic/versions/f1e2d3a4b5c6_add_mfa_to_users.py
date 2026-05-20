"""add MFA TOTP columns to users

Three columns, all nullable so existing users (and the migration deploy
itself) don't break:

* `mfa_secret` — Fernet-encrypted base32 TOTP secret. Stays NULL until
  the super_admin enrolls. Encryption uses an HKDF-derived sub-key from
  SECRET_KEY (see services/mfa.py); rotating SECRET_KEY invalidates
  every enrollment, which is the expected behaviour at that scale.
* `mfa_enabled` — bool. We flip to true only after the user types a
  valid TOTP code during enrollment (proves the QR scan succeeded).
* `mfa_recovery_codes` — JSON list of `{hash, used_at}` objects, one
  per backup code. 10 codes minted at enrollment, bcrypt-hashed,
  single-use.

Enrollment is forced for super_admins (load_current_user middleware);
optional + opt-in for the other roles. The column is therefore set on
super_admins first, but the schema is identical for all roles so we
can flip the policy later without another migration.

Revision ID: f1e2d3a4b5c6
Revises: c4d2e8f7a1b9
Create Date: 2026-05-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f1e2d3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c4d2e8f7a1b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_secret", sa.String(length=500), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "mfa_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("users", sa.Column("mfa_recovery_codes", sa.JSON(), nullable=True))
    op.add_column("users", sa.Column("mfa_enrolled_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "mfa_enrolled_at")
    op.drop_column("users", "mfa_recovery_codes")
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "mfa_secret")
