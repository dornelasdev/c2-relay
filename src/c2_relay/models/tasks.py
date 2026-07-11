"""Task lifecycle contracts and state transitions."""

from datetime import datetime
from enum import StrEnum

from pydantic import model_validator

from c2_relay.models.actions import ActionRequest
from c2_relay.models.common import AgentId, DomainModel, TaskId, UtcDateTime


class TaskStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset({TaskStatus.CLAIMED, TaskStatus.EXPIRED, TaskStatus.CANCELLED}),
    TaskStatus.CLAIMED: frozenset(
        {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.EXPIRED, TaskStatus.CANCELLED}
    ),
    TaskStatus.RUNNING: frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.EXPIRED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


class Task(DomainModel):
    id: TaskId
    agent_id: AgentId
    action: ActionRequest
    status: TaskStatus = TaskStatus.QUEUED
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> "Task":
        if self.updated_at < self.created_at:
            msg = "updated_at cannot precede created_at"
            raise ValueError(msg)
        return self


class InvalidTaskTransitionError(ValueError):
    """Raised when a requested task state change violates the lifecycle."""

    def __init__(self, current: TaskStatus, requested: TaskStatus) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"cannot transition task from {current.value} to {requested.value}")


def transition_task(task: Task, requested: TaskStatus, *, at: datetime) -> Task:
    """Return a new task in the requested state after validating the transition."""

    if requested not in _ALLOWED_TRANSITIONS[task.status]:
        raise InvalidTaskTransitionError(task.status, requested)
    values = task.model_dump()
    values.update(status=requested, updated_at=at)
    return Task.model_validate(values)
