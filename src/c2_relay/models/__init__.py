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
from c2_relay.models.agents import AgentMetadata, RegisteredAgent
from c2_relay.models.common import AgentId, DomainModel, TaskId, UtcDateTime
from c2_relay.models.results import ActionFailure, ActionResult, ActionSuccess, ErrorDetail
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
    "CurrentUserAction",
    "CurrentUserOutput",
    "DomainModel",
    "ErrorDetail",
    "HostnameAction",
    "HostnameOutput",
    "InvalidTaskTransitionError",
    "OperatingSystemAction",
    "OperatingSystemOutput",
    "RegisteredAgent",
    "Task",
    "TaskId",
    "TaskStatus",
    "UtcDateTime",
    "transition_task",
]
