"""Relational persistence schema."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from c2_relay.db.types import UTCDateTime


class Base(DeclarativeBase):
    pass


class AgentRow(Base):
    __tablename__ = "agents"
    __table_args__ = (CheckConstraint("status IN ('active','disabled')", name="ck_agents_status"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    credential_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    operating_system: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    architecture: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), server_default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class OperatorRow(Base):
    __tablename__ = "operators"
    __table_args__ = (
        CheckConstraint("status IN ('active','disabled')", name="ck_operators_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    credential_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_authenticated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
            "'agent.enrolled','agent.metadata_updated','agent.disabled',"
            "'task.created','task.claimed','task.cancelled','task.completed','task.failed'"
            ")",
            name="ck_audit_events_type",
        ),
        Index("ix_audit_events_occurred", "occurred_at", "id"),
        Index("ix_audit_events_type_occurred", "event_type", "occurred_at"),
        Index("ix_audit_events_operator_occurred", "operator_id", "occurred_at"),
        Index("ix_audit_events_agent_occurred", "agent_id", "occurred_at"),
        Index("ix_audit_events_task_occurred", "task_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    operator_id: Mapped[UUID | None] = mapped_column(Uuid)
    agent_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(Uuid)


class TaskRow(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','claimed','running','completed','failed','expired','cancelled')",
            name="ck_tasks_status",
        ),
        UniqueConstraint(
            "created_by_operator_id",
            "idempotency_key_digest",
            name="uq_tasks_operator_idempotency_key",
        ),
        Index("ix_tasks_agent_status_created", "agent_id", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    created_by_operator_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("operators.id", ondelete="RESTRICT")
    )
    idempotency_key_digest: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class TaskResultRow(Base):
    __tablename__ = "task_results"
    __table_args__ = (
        CheckConstraint("status IN ('completed','failed')", name="ck_task_results_status"),
    )

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=func.current_timestamp(),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
