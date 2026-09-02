"""Bounded, non-sensitive operational audit contracts."""

from enum import StrEnum
from typing import NewType
from uuid import UUID

from pydantic import model_validator

from c2_relay.models.common import AgentId, DomainModel, TaskId, UtcDateTime
from c2_relay.models.operators import OperatorId

AuditEventId = NewType("AuditEventId", UUID)


class AuditEventType(StrEnum):
    AGENT_ENROLLED = "agent.enrolled"
    AGENT_METADATA_UPDATED = "agent.metadata_updated"
    AGENT_DISABLED = "agent.disabled"
    TASK_CREATED = "task.created"
    TASK_CLAIMED = "task.claimed"
    TASK_CANCELLED = "task.cancelled"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"


_OPERATOR_EVENTS = {
    AuditEventType.AGENT_DISABLED,
    AuditEventType.TASK_CREATED,
    AuditEventType.TASK_CANCELLED,
}
_TASK_EVENTS = {
    AuditEventType.TASK_CREATED,
    AuditEventType.TASK_CLAIMED,
    AuditEventType.TASK_CANCELLED,
    AuditEventType.TASK_COMPLETED,
    AuditEventType.TASK_FAILED,
}


class AuditEvent(DomainModel):
    id: AuditEventId
    event_type: AuditEventType
    occurred_at: UtcDateTime
    operator_id: OperatorId | None = None
    agent_id: AgentId
    task_id: TaskId | None = None

    @model_validator(mode="after")
    def references_match_event_type(self) -> "AuditEvent":
        operator_required = self.event_type in _OPERATOR_EVENTS
        if (self.operator_id is not None) != operator_required:
            msg = "operator reference does not match event type"
            raise ValueError(msg)
        task_required = self.event_type in _TASK_EVENTS
        if (self.task_id is not None) != task_required:
            msg = "task reference does not match event type"
            raise ValueError(msg)
        return self
