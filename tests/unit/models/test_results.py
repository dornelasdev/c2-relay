from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from c2_relay.models import (
    ActionFailure,
    ActionResult,
    ActionSuccess,
    AgentId,
    ErrorDetail,
    HostnameOutput,
    TaskId,
)

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
TASK_ID = TaskId(UUID("10000000-0000-4000-8000-000000000001"))
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))


def test_success_result_contains_typed_output() -> None:
    result = ActionSuccess(
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        completed_at=NOW,
        output=HostnameOutput(hostname="relay-host"),
    )

    parsed: ActionResult = TypeAdapter(ActionResult).validate_python(result.model_dump())

    assert isinstance(parsed, ActionSuccess)
    assert isinstance(parsed.output, HostnameOutput)


def test_failure_result_contains_bounded_error_details() -> None:
    result = ActionFailure(
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        completed_at=NOW,
        error=ErrorDetail(code="action_failed", message="Unable to read host data"),
    )

    parsed: ActionResult = TypeAdapter(ActionResult).validate_python(result.model_dump())

    assert isinstance(parsed, ActionFailure)
    assert parsed.error.retryable is False


@pytest.mark.parametrize("code", ["UPPERCASE", "contains-dash", ""])
def test_error_code_uses_a_stable_machine_readable_format(code: str) -> None:
    with pytest.raises(ValidationError):
        ErrorDetail(code=code, message="failure")


def test_result_rejects_an_unknown_status() -> None:
    with pytest.raises(ValidationError, match="Input tag 'pending'"):
        TypeAdapter(ActionResult).validate_python({"status": "pending"})
