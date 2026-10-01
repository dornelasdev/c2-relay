from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from c2_relay.models import AgentId, EnrollmentGrant, EnrollmentGrantId, OperatorId

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
GRANT_ID = EnrollmentGrantId(UUID("50000000-0000-4000-8000-000000000001"))
OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))


def grant_values() -> dict[str, object]:
    return {
        "id": GRANT_ID,
        "issued_by_operator_id": OPERATOR_ID,
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
    }


def test_grant_is_a_non_secret_immutable_record() -> None:
    grant = EnrollmentGrant.model_validate(grant_values())

    assert grant.redeemed_at is None and grant.revoked_at is None
    assert "token" not in grant.model_dump_json()
    assert "digest" not in grant.model_dump_json()
    with pytest.raises(ValidationError, match="frozen"):
        grant.expires_at = NOW


@pytest.mark.parametrize(
    "outcome",
    [
        {"redeemed_at": NOW, "redeemed_by_agent_id": AGENT_ID},
        {"redeemed_at": NOW + timedelta(minutes=14), "redeemed_by_agent_id": AGENT_ID},
        {"revoked_at": NOW, "revoked_by_operator_id": OPERATOR_ID},
        {"revoked_at": NOW + timedelta(hours=1), "revoked_by_operator_id": OPERATOR_ID},
    ],
)
def test_grant_accepts_consistent_terminal_records(outcome: dict[str, object]) -> None:
    grant = EnrollmentGrant.model_validate(grant_values() | outcome)

    for field, value in outcome.items():
        assert getattr(grant, field) == value


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"expires_at": NOW}, "expires_at must follow"),
        ({"expires_at": NOW - timedelta(seconds=1)}, "expires_at must follow"),
        ({"redeemed_at": NOW}, "redemption requires both"),
        ({"redeemed_by_agent_id": AGENT_ID}, "redemption requires both"),
        ({"revoked_at": NOW}, "revocation requires both"),
        ({"revoked_by_operator_id": OPERATOR_ID}, "revocation requires both"),
        (
            {
                "redeemed_at": NOW,
                "redeemed_by_agent_id": AGENT_ID,
                "revoked_at": NOW,
                "revoked_by_operator_id": OPERATOR_ID,
            },
            "cannot be both",
        ),
        (
            {"redeemed_at": NOW - timedelta(seconds=1), "redeemed_by_agent_id": AGENT_ID},
            "within the grant lifetime",
        ),
        (
            {"redeemed_at": NOW + timedelta(minutes=15), "redeemed_by_agent_id": AGENT_ID},
            "within the grant lifetime",
        ),
        (
            {"revoked_at": NOW - timedelta(seconds=1), "revoked_by_operator_id": OPERATOR_ID},
            "revoked_at cannot precede",
        ),
        ({"created_at": NOW.replace(tzinfo=None)}, "timestamp must include a UTC offset"),
        ({"token_digest": "a" * 64}, "Extra inputs are not permitted"),
    ],
)
def test_grant_rejects_invalid_records(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        EnrollmentGrant.model_validate(grant_values() | changes)
