"""Structured action result contracts."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from c2_relay.models.actions import ActionOutput
from c2_relay.models.common import AgentId, DomainModel, TaskId, UtcDateTime

ErrorCode = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")]
ErrorMessage = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class ErrorDetail(DomainModel):
    code: ErrorCode
    message: ErrorMessage
    retryable: bool = False


class ActionSuccess(DomainModel):
    status: Literal["completed"] = "completed"
    task_id: TaskId
    agent_id: AgentId
    completed_at: UtcDateTime
    output: ActionOutput


class ActionFailure(DomainModel):
    status: Literal["failed"] = "failed"
    task_id: TaskId
    agent_id: AgentId
    completed_at: UtcDateTime
    error: ErrorDetail


ActionResult = Annotated[ActionSuccess | ActionFailure, Field(discriminator="status")]
