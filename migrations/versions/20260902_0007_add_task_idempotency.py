"""Add operator-scoped task idempotency."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0007"
down_revision: str | None = "20260902_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("created_by_operator_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("idempotency_key_digest", sa.String(length=64), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_tasks_created_by_operator_id_operators",
            "operators",
            ["created_by_operator_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_tasks_operator_idempotency_key",
            ["created_by_operator_id", "idempotency_key_digest"],
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_constraint(
            "uq_tasks_operator_idempotency_key",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_tasks_created_by_operator_id_operators",
            type_="foreignkey",
        )
        batch_op.drop_column("idempotency_key_digest")
        batch_op.drop_column("created_by_operator_id")
