import pytest
from pydantic import ValidationError

from c2_relay.models import AgentMetadata


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
