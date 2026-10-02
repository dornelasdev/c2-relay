"""Extend audit events for enrollment grant operations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_0010"
down_revision: str | None = "20261001_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_TYPES = (
    "event_type IN ("
    "'agent.enrolled','agent.metadata_updated','agent.disabled',"
    "'task.created','task.claimed','task.cancelled','task.completed','task.failed',"
    "'enrollment_grant.issued','enrollment_grant.revoked','enrollment_grant.redeemed'"
    ")"
)
_LEGACY_EVENT_TYPES = (
    "event_type IN ("
    "'agent.enrolled','agent.metadata_updated','agent.disabled',"
    "'task.created','task.claimed','task.cancelled','task.completed','task.failed'"
    ")"
)


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.drop_constraint("ck_audit_events_type", type_="check")
        batch_op.alter_column("agent_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.add_column(sa.Column("enrollment_grant_id", sa.Uuid(), nullable=True))
        batch_op.create_check_constraint("ck_audit_events_type", _EVENT_TYPES)
        batch_op.create_index(
            "ix_audit_events_grant_occurred",
            ["enrollment_grant_id", "occurred_at"],
        )


def downgrade() -> None:
    audit_events = sa.table(
        "audit_events",
        sa.column("event_type", sa.String()),
    )
    op.execute(audit_events.delete().where(audit_events.c.event_type.like("enrollment_grant.%")))
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.drop_index("ix_audit_events_grant_occurred")
        batch_op.drop_constraint("ck_audit_events_type", type_="check")
        batch_op.drop_column("enrollment_grant_id")
        batch_op.alter_column("agent_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.create_check_constraint("ck_audit_events_type", _LEGACY_EVENT_TYPES)
