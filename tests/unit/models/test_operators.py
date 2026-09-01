from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from c2_relay.models import Operator, OperatorId, OperatorStatus

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))


def test_operator_has_a_bounded_non_secret_identity() -> None:
    operator = Operator(id=OPERATOR_ID, name=" Primary Operator ", created_at=NOW)

    assert operator.name == "Primary Operator"
    assert operator.status is OperatorStatus.ACTIVE
    assert operator.last_authenticated_at is None


def test_operator_can_represent_a_disabled_identity() -> None:
    authenticated_at = NOW + timedelta(minutes=1)
    operator = Operator(
        id=OPERATOR_ID,
        name="Primary Operator",
        status=OperatorStatus.DISABLED,
        created_at=NOW,
        last_authenticated_at=authenticated_at,
    )

    assert operator.status is OperatorStatus.DISABLED
    assert operator.last_authenticated_at == authenticated_at


@pytest.mark.parametrize("name", ["", "x" * 101, "line\nbreak", 42])
def test_operator_rejects_invalid_names(name: object) -> None:
    with pytest.raises(ValidationError):
        Operator.model_validate({"id": OPERATOR_ID, "name": name, "created_at": NOW})


def test_operator_rejects_authentication_before_creation() -> None:
    with pytest.raises(ValidationError, match="last_authenticated_at cannot precede created_at"):
        Operator(
            id=OPERATOR_ID,
            name="Primary Operator",
            created_at=NOW,
            last_authenticated_at=NOW - timedelta(seconds=1),
        )
