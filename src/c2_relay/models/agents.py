"""Agent identity and host metadata contracts."""

from typing import Annotated

from pydantic import Field, StringConstraints

from c2_relay.models.common import AgentId, DomainModel, UtcDateTime

HostFact = Annotated[str, StringConstraints(min_length=1, max_length=255)]
AgentVersion = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=32,
        pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$",
    ),
]


class AgentMetadata(DomainModel):
    """Bounded facts reported by an agent during enrollment or check-in."""

    hostname: HostFact
    operating_system: HostFact
    username: HostFact
    agent_version: AgentVersion
    architecture: HostFact | None = Field(default=None)


class RegisteredAgent(DomainModel):
    id: AgentId
    metadata: AgentMetadata
    created_at: UtcDateTime
    last_seen_at: UtcDateTime
