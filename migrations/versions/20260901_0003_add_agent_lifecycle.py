"""Add persistent agent lifecycle state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0003"
down_revision: str | None = "20260901_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(length=16), server_default="active", nullable=False)
        )
        batch_op.add_column(sa.Column("disabled_at", sa.DateTime(), nullable=True))
        batch_op.create_check_constraint(
            "ck_agents_status",
            "status IN ('active','disabled')",
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_constraint("ck_agents_status", type_="check")
        batch_op.drop_column("disabled_at")
        batch_op.drop_column("status")
