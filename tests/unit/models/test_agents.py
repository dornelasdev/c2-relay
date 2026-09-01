from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from c2_relay.models import (
    AgentId,
    AgentMetadata,
    AgentStatus,
    InvalidAgentTransitionError,
    RegisteredAgent,
    disable_agent,
)

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))


def metadata() -> AgentMetadata:
    return AgentMetadata(
        hostname="relay-host",
        operating_system="Linux",
        username="operator",
        agent_version="0.2.0",
    )


def test_agent_metadata_strips_surrounding_whitespace() -> None:
    metadata = AgentMetadata(
        hostname=" relay-host ",
        operating_system=" Linux ",
        username=" operator ",
        agent_version="0.1.0",
        architecture=" arm64 ",
    )

    assert metadata.hostname == "relay-host"
    assert metadata.operating_system == "Linux"
    assert metadata.username == "operator"
    assert metadata.architecture == "arm64"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hostname", ""),
        ("operating_system", "x" * 256),
        ("username", 42),
        ("agent_version", "latest"),
        ("agent_version", "1.0.0" + "-" + "x" * 32),
    ],
)
def test_agent_metadata_rejects_invalid_host_facts(field: str, value: object) -> None:
    values: dict[str, object] = {
        "hostname": "relay-host",
        "operating_system": "Linux",
        "username": "operator",
        "agent_version": "0.1.0",
    }
    values[field] = value

    with pytest.raises(ValidationError):
        AgentMetadata.model_validate(values)


def test_agent_metadata_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentMetadata.model_validate(
            {
                "hostname": "relay-host",
                "operating_system": "Linux",
                "username": "operator",
                "agent_version": "0.1.0",
                "ip_address": "192.0.2.1",
            }
        )


def test_agent_metadata_is_immutable() -> None:
    metadata = AgentMetadata(
        hostname="relay-host",
        operating_system="Linux",
        username="operator",
        agent_version="0.1.0",
    )

    with pytest.raises(ValidationError, match="Instance is frozen"):
        metadata.__setattr__("hostname", "changed")


def test_registered_agent_defaults_to_active() -> None:
    agent = RegisteredAgent(
        id=AGENT_ID,
        metadata=metadata(),
        created_at=NOW,
        last_seen_at=NOW,
    )

    assert agent.status is AgentStatus.ACTIVE
    assert agent.disabled_at is None


def test_registered_agent_can_represent_a_disabled_identity() -> None:
    disabled_at = NOW + timedelta(minutes=5)
    agent = RegisteredAgent(
        id=AGENT_ID,
        metadata=metadata(),
        status=AgentStatus.DISABLED,
        created_at=NOW,
        last_seen_at=NOW,
        disabled_at=disabled_at,
    )

    assert agent.status is AgentStatus.DISABLED
    assert agent.disabled_at == disabled_at


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"last_seen_at": NOW - timedelta(seconds=1)}, "last_seen_at cannot precede created_at"),
        ({"disabled_at": NOW}, "active agent cannot have disabled_at"),
        ({"status": AgentStatus.DISABLED}, "disabled agent requires disabled_at"),
        (
            {
                "status": AgentStatus.DISABLED,
                "last_seen_at": NOW + timedelta(minutes=2),
                "disabled_at": NOW + timedelta(minutes=1),
            },
            "disabled_at cannot precede last_seen_at",
        ),
    ],
)
def test_registered_agent_rejects_impossible_lifecycle_history(
    overrides: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "id": AGENT_ID,
        "metadata": metadata(),
        "created_at": NOW,
        "last_seen_at": NOW,
    }
    values.update(overrides)

    with pytest.raises(ValidationError, match=message):
        RegisteredAgent.model_validate(values)


def test_disable_agent_returns_a_new_disabled_identity() -> None:
    agent = RegisteredAgent(
        id=AGENT_ID,
        metadata=metadata(),
        created_at=NOW,
        last_seen_at=NOW,
    )
    disabled_at = NOW + timedelta(minutes=1)

    disabled = disable_agent(agent, at=disabled_at)

    assert agent.status is AgentStatus.ACTIVE
    assert disabled.status is AgentStatus.DISABLED
    assert disabled.disabled_at == disabled_at


def test_disable_agent_rejects_an_invalid_transition() -> None:
    agent = RegisteredAgent(
        id=AGENT_ID,
        metadata=metadata(),
        status=AgentStatus.DISABLED,
        created_at=NOW,
        last_seen_at=NOW,
        disabled_at=NOW,
    )

    with pytest.raises(InvalidAgentTransitionError, match="only active agents"):
        disable_agent(agent, at=NOW)
