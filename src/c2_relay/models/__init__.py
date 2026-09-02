"""Public domain contracts for agents, actions, tasks, and results."""

from c2_relay.models.actions import (
    ActionKind,
    ActionOutput,
    ActionRequest,
    CurrentUserAction,
    CurrentUserOutput,
    HostnameAction,
    HostnameOutput,
    OperatingSystemAction,
    OperatingSystemOutput,
)
from c2_relay.models.agents import (
    AgentMetadata,
    AgentStatus,
    InvalidAgentTransitionError,
    RegisteredAgent,
    disable_agent,
)
from c2_relay.models.audit import AuditEvent, AuditEventId, AuditEventType
from c2_relay.models.common import AgentId, DomainModel, TaskId, UtcDateTime
from c2_relay.models.operators import Operator, OperatorId, OperatorStatus
from c2_relay.models.results import (
    ActionFailure,
    ActionResult,
    ActionSuccess,
    ErrorDetail,
    StoredActionResult,
)
from c2_relay.models.tasks import InvalidTaskTransitionError, Task, TaskStatus, transition_task

__all__ = [
    "ActionFailure",
    "ActionKind",
    "ActionOutput",
    "ActionRequest",
    "ActionResult",
    "ActionSuccess",
    "AgentId",
    "AgentMetadata",
    "AgentStatus",
    "AuditEvent",
    "AuditEventId",
    "AuditEventType",
    "CurrentUserAction",
    "CurrentUserOutput",
    "DomainModel",
    "ErrorDetail",
    "HostnameAction",
    "HostnameOutput",
    "InvalidAgentTransitionError",
    "InvalidTaskTransitionError",
    "OperatingSystemAction",
    "OperatingSystemOutput",
    "Operator",
    "OperatorId",
    "OperatorStatus",
    "RegisteredAgent",
    "StoredActionResult",
    "Task",
    "TaskId",
    "TaskStatus",
    "UtcDateTime",
    "disable_agent",
    "transition_task",
]
