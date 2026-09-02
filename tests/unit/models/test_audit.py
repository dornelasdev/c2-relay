from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from c2_relay.models import (
    AgentId,
    AuditEvent,
    AuditEventId,
    AuditEventType,
    OperatorId,
    TaskId,
)

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
EVENT_ID = AuditEventId(UUID("40000000-0000-4000-8000-000000000001"))
OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))
TASK_ID = TaskId(UUID("10000000-0000-4000-8000-000000000001"))


@pytest.mark.parametrize(
    ("event_type", "has_operator", "has_task"),
    [
        (AuditEventType.AGENT_ENROLLED, False, False),
        (AuditEventType.AGENT_METADATA_UPDATED, False, False),
        (AuditEventType.AGENT_DISABLED, True, False),
        (AuditEventType.TASK_CREATED, True, True),
        (AuditEventType.TASK_CLAIMED, False, True),
        (AuditEventType.TASK_CANCELLED, True, True),
        (AuditEventType.TASK_COMPLETED, False, True),
        (AuditEventType.TASK_FAILED, False, True),
    ],
)
def test_audit_event_requires_expected_references(
    event_type: AuditEventType,
    has_operator: bool,
    has_task: bool,
) -> None:
    event = AuditEvent(
        id=EVENT_ID,
        event_type=event_type,
        occurred_at=NOW,
        operator_id=OPERATOR_ID if has_operator else None,
        agent_id=AGENT_ID,
        task_id=TASK_ID if has_task else None,
    )

    assert event.event_type is event_type


@pytest.mark.parametrize(
    "values",
    [
        {
            "event_type": AuditEventType.AGENT_ENROLLED,
            "operator_id": OPERATOR_ID,
            "task_id": None,
        },
        {
            "event_type": AuditEventType.TASK_CREATED,
            "operator_id": OPERATOR_ID,
            "task_id": None,
        },
    ],
)
def test_audit_event_rejects_mismatched_references(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="reference does not match event type"):
        AuditEvent.model_validate(
            {
                "id": EVENT_ID,
                "occurred_at": NOW,
                "agent_id": AGENT_ID,
                **values,
            }
        )
