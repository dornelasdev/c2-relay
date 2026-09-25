"""Recover claimed tasks that predate task leases."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260925_0008"
down_revision: str | None = "20260902_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tasks = sa.table(
        "tasks",
        sa.column("status", sa.String()),
        sa.column("updated_at", sa.DateTime()),
        sa.column("lease_expires_at", sa.DateTime()),
    )
    op.execute(
        tasks.update()
        .where(tasks.c.status == "claimed", tasks.c.lease_expires_at.is_(None))
        .values(status="queued", updated_at=sa.func.current_timestamp())
    )


def downgrade() -> None:
    # The original claim state cannot be distinguished from later queued work.
    pass
