"""Non-secret, server-scoped operator selections."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from c2_relay.cli.client import normalize_server_url
from c2_relay.core.storage import AtomicJsonStore
from c2_relay.models import AgentId, OperatorId, TaskId


class OperatorContextError(RuntimeError):
    """Operator context could not be used or safely persisted."""


class OperatorContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_url: str
    operator_id: OperatorId
    selected_agent_id: AgentId | None = None
    last_task_id: TaskId | None = None

    @field_validator("server_url")
    @classmethod
    def normalize_origin(cls, value: str) -> str:
        return normalize_server_url(value)


class OperatorContextStore:
    def __init__(self, path: Path) -> None:
        self._store = AtomicJsonStore(path)

    def _read(self) -> OperatorContext | None:
        try:
            payload = self._store.load()
            return None if payload is None else OperatorContext.model_validate(payload)
        except (OSError, ValueError):
            raise OperatorContextError(
                "Cannot read operator context. Check its format, path, and owner-only permissions."
            ) from None

    def load(self, server_url: str, operator_id: OperatorId) -> OperatorContext:
        empty = OperatorContext(server_url=server_url, operator_id=operator_id)
        stored = self._read()
        if (
            stored is None
            or stored.server_url != empty.server_url
            or stored.operator_id != operator_id
        ):
            return empty
        return stored

    def save(self, context: OperatorContext) -> None:
        self._read()
        try:
            self._store.save(context.model_dump(mode="json"))
        except OSError:
            raise OperatorContextError(
                "Cannot save operator context. Check the context path and write permissions."
            ) from None
