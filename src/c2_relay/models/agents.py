"""Agent identity, host metadata, and lifecycle contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

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


class AgentStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class RegisteredAgent(DomainModel):
    id: AgentId
    metadata: AgentMetadata
    status: AgentStatus = AgentStatus.ACTIVE
    created_at: UtcDateTime
    last_seen_at: UtcDateTime
    disabled_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def lifecycle_timestamps_are_consistent(self) -> "RegisteredAgent":
        if self.last_seen_at < self.created_at:
            msg = "last_seen_at cannot precede created_at"
            raise ValueError(msg)
        if self.status is AgentStatus.ACTIVE and self.disabled_at is not None:
            msg = "active agent cannot have disabled_at"
            raise ValueError(msg)
        if self.status is AgentStatus.DISABLED and self.disabled_at is None:
            msg = "disabled agent requires disabled_at"
            raise ValueError(msg)
        if self.disabled_at is not None and self.disabled_at < self.last_seen_at:
            msg = "disabled_at cannot precede last_seen_at"
            raise ValueError(msg)
        return self


class InvalidAgentTransitionError(ValueError):
    """Raised when a requested agent state change violates the lifecycle."""


def disable_agent(agent: RegisteredAgent, *, at: datetime) -> RegisteredAgent:
    if agent.status is not AgentStatus.ACTIVE:
        raise InvalidAgentTransitionError("only active agents can be disabled")
    values = agent.model_dump()
    values.update(status=AgentStatus.DISABLED, disabled_at=at)
    return RegisteredAgent.model_validate(values)
