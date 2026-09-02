"""Add append-only operational audit events."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0006"
down_revision: str | None = "20260901_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "event_type IN ("
            "'agent.enrolled','agent.metadata_updated','agent.disabled',"
            "'task.created','task.claimed','task.cancelled','task.completed','task.failed'"
            ")",
            name="ck_audit_events_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_occurred",
        "audit_events",
        ["occurred_at", "id"],
    )
    op.create_index(
        "ix_audit_events_type_occurred",
        "audit_events",
        ["event_type", "occurred_at"],
    )
    op.create_index(
        "ix_audit_events_operator_occurred",
        "audit_events",
        ["operator_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_events_agent_occurred",
        "audit_events",
        ["agent_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_events_task_occurred",
        "audit_events",
        ["task_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("audit_events")
