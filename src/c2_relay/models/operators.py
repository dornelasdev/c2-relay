"""Operator identity contracts."""

from enum import StrEnum
from typing import Annotated, NewType
from uuid import UUID

from pydantic import StringConstraints, model_validator

from c2_relay.models.common import DomainModel, UtcDateTime

OperatorId = NewType("OperatorId", UUID)
OperatorName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$"),
]


class OperatorStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class Operator(DomainModel):
    """Persistent, non-secret identity for a human operator."""

    id: OperatorId
    name: OperatorName
    status: OperatorStatus = OperatorStatus.ACTIVE
    created_at: UtcDateTime
    last_authenticated_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def authentication_timestamp_follows_creation(self) -> "Operator":
        if self.last_authenticated_at is not None and self.last_authenticated_at < self.created_at:
            msg = "last_authenticated_at cannot precede created_at"
            raise ValueError(msg)
        return self
