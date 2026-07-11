from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from c2_relay.models import (
    AgentId,
    HostnameAction,
    InvalidTaskTransitionError,
    Task,
    TaskId,
    TaskStatus,
    transition_task,
)

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
TASK_ID = TaskId(UUID("10000000-0000-4000-8000-000000000001"))
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))

ALLOWED_TRANSITIONS = {
    TaskStatus.QUEUED: {TaskStatus.CLAIMED, TaskStatus.EXPIRED, TaskStatus.CANCELLED},
    TaskStatus.CLAIMED: {
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.EXPIRED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.EXPIRED: set(),
    TaskStatus.CANCELLED: set(),
}


def make_task(status: TaskStatus = TaskStatus.QUEUED) -> Task:
    return Task(
        id=TASK_ID,
        agent_id=AGENT_ID,
        action=HostnameAction(),
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def test_task_defaults_to_queued() -> None:
    task = Task(
        id=TASK_ID,
        agent_id=AGENT_ID,
        action=HostnameAction(),
        created_at=NOW,
        updated_at=NOW,
    )

    assert task.status is TaskStatus.QUEUED


def test_task_rejects_reversed_timestamps() -> None:
    with pytest.raises(ValidationError, match="updated_at cannot precede created_at"):
        Task(
            id=TASK_ID,
            agent_id=AGENT_ID,
            action=HostnameAction(),
            created_at=NOW,
            updated_at=NOW - timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (current, requested)
        for current, allowed in ALLOWED_TRANSITIONS.items()
        for requested in allowed
    ],
)
def test_task_allows_declared_transitions(current: TaskStatus, requested: TaskStatus) -> None:
    task = make_task(current)
    changed_at = NOW + timedelta(seconds=1)

    transitioned = transition_task(task, requested, at=changed_at)

    assert transitioned.status is requested
    assert transitioned.updated_at == changed_at
    assert task.status is current


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (current, requested)
        for current, allowed in ALLOWED_TRANSITIONS.items()
        for requested in TaskStatus
        if requested not in allowed
    ],
)
def test_task_rejects_undeclared_transitions(current: TaskStatus, requested: TaskStatus) -> None:
    with pytest.raises(InvalidTaskTransitionError) as raised:
        transition_task(make_task(current), requested, at=NOW + timedelta(seconds=1))

    assert raised.value.current is current
    assert raised.value.requested is requested
    assert str(raised.value) == f"cannot transition task from {current.value} to {requested.value}"


@pytest.mark.parametrize("at", [NOW - timedelta(seconds=1), datetime(2026, 1, 2, 13)])
def test_transition_revalidates_its_timestamp(at: datetime) -> None:
    with pytest.raises(ValidationError):
        transition_task(make_task(), TaskStatus.CLAIMED, at=at)
