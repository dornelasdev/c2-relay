"""Transport-specific request and response contracts."""

from typing import Annotated

from pydantic import ConfigDict, Field

from c2_relay.models import (
    ActionFailure,
    ActionRequest,
    ActionSuccess,
    AgentId,
    AgentMetadata,
    DomainModel,
    Task,
)


class TransportModel(DomainModel):
    """JSON-facing model that parses wire primitives into strict domain types."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=False, str_strip_whitespace=True)


class EnrollmentRequest(AgentMetadata):
    model_config = TransportModel.model_config


class EnrollmentResponse(DomainModel):
    agent_id: AgentId
    credential: Annotated[str, Field(min_length=32, max_length=128)]


class CheckInResponse(DomainModel):
    agent_id: AgentId
    accepted: bool = True


class TaskCreateRequest(TransportModel):
    agent_id: AgentId
    action: ActionRequest


class TaskResponse(DomainModel):
    task: Task | None


class HealthResponse(DomainModel):
    status: str = "ok"


class ActionSuccessSubmission(ActionSuccess):
    model_config = TransportModel.model_config


class ActionFailureSubmission(ActionFailure):
    model_config = TransportModel.model_config


ActionResultSubmission = Annotated[
    ActionSuccessSubmission | ActionFailureSubmission,
    Field(discriminator="status"),
]
