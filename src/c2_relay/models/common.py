"""Shared domain primitives."""

from datetime import UTC, datetime
from typing import Annotated, NewType
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict

AgentId = NewType("AgentId", UUID)
TaskId = NewType("TaskId", UUID)


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "timestamp must include a UTC offset"
        raise ValueError(msg)
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_normalize_utc)]


class DomainModel(BaseModel):
    """Strict, immutable base for values crossing domain boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)
