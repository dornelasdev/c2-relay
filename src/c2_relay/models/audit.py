"""Bounded, non-sensitive operational audit contracts."""

from enum import StrEnum
from typing import NewType
from uuid import UUID

from pydantic import model_validator

from c2_relay.models.common import AgentId, DomainModel, TaskId, UtcDateTime
from c2_relay.models.enrollment import EnrollmentGrantId
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
    ENROLLMENT_GRANT_ISSUED = "enrollment_grant.issued"
    ENROLLMENT_GRANT_REVOKED = "enrollment_grant.revoked"
    ENROLLMENT_GRANT_REDEEMED = "enrollment_grant.redeemed"


_OPERATOR_EVENTS = {
    AuditEventType.AGENT_DISABLED,
    AuditEventType.TASK_CREATED,
    AuditEventType.TASK_CANCELLED,
    AuditEventType.ENROLLMENT_GRANT_ISSUED,
    AuditEventType.ENROLLMENT_GRANT_REVOKED,
}
_TASK_EVENTS = {
    AuditEventType.TASK_CREATED,
    AuditEventType.TASK_CLAIMED,
    AuditEventType.TASK_CANCELLED,
    AuditEventType.TASK_COMPLETED,
    AuditEventType.TASK_FAILED,
}
_GRANT_EVENTS = {
    AuditEventType.ENROLLMENT_GRANT_ISSUED,
    AuditEventType.ENROLLMENT_GRANT_REVOKED,
    AuditEventType.ENROLLMENT_GRANT_REDEEMED,
}
_AGENT_EVENTS = set(AuditEventType) - {
    AuditEventType.ENROLLMENT_GRANT_ISSUED,
    AuditEventType.ENROLLMENT_GRANT_REVOKED,
}


class AuditEvent(DomainModel):
    id: AuditEventId
    event_type: AuditEventType
    occurred_at: UtcDateTime
    operator_id: OperatorId | None = None
    agent_id: AgentId | None = None
    task_id: TaskId | None = None
    enrollment_grant_id: EnrollmentGrantId | None = None

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
        grant_required = self.event_type in _GRANT_EVENTS
        if (self.enrollment_grant_id is not None) != grant_required:
            raise ValueError("enrollment grant reference does not match event type")
        agent_required = self.event_type in _AGENT_EVENTS
        if (self.agent_id is not None) != agent_required:
            raise ValueError("agent reference does not match event type")
        return self
