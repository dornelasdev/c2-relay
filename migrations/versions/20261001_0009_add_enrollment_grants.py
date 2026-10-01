"""Add single-use enrollment grant records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261001_0009"
down_revision: str | None = "20260925_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enrollment_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("issued_by_operator_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
        sa.Column("redeemed_by_agent_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by_operator_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("expires_at > created_at", name="ck_enrollment_grants_expiry"),
        sa.CheckConstraint(
            "(redeemed_at IS NULL AND redeemed_by_agent_id IS NULL) OR "
            "(redeemed_at IS NOT NULL AND redeemed_by_agent_id IS NOT NULL "
            "AND redeemed_at >= created_at AND redeemed_at < expires_at)",
            name="ck_enrollment_grants_redemption",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_operator_id IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_operator_id IS NOT NULL "
            "AND revoked_at >= created_at)",
            name="ck_enrollment_grants_revocation",
        ),
        sa.CheckConstraint(
            "redeemed_at IS NULL OR revoked_at IS NULL", name="ck_enrollment_grants_terminal"
        ),
        sa.CheckConstraint("length(token_digest) = 64", name="ck_enrollment_grants_digest_length"),
        sa.ForeignKeyConstraint(["issued_by_operator_id"], ["operators.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["redeemed_by_agent_id"], ["agents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["revoked_by_operator_id"], ["operators.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest", name="uq_enrollment_grants_token_digest"),
    )
    op.create_index("ix_enrollment_grants_created", "enrollment_grants", ["created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_enrollment_grants_created", table_name="enrollment_grants")
    op.drop_table("enrollment_grants")
