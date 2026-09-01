"""Add persistent operator identities."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0002"
down_revision: str | None = "20260711_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operators",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("credential_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_authenticated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_operators_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_digest"),
    )


def downgrade() -> None:
    op.drop_table("operators")
