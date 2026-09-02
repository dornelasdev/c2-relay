"""Add server-controlled result receipt timestamps."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0005"
down_revision: str | None = "20260901_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task_results") as batch_op:
        batch_op.add_column(
            sa.Column(
                "received_at",
                sa.DateTime(),
                server_default=sa.func.current_timestamp(),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("task_results") as batch_op:
        batch_op.drop_column("received_at")
