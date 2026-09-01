"""Durable local outbox for one pending action result."""

from pathlib import Path

from pydantic import TypeAdapter

from c2_relay.agent.storage import AtomicJsonStore
from c2_relay.models import ActionResult

_RESULT_ADAPTER: TypeAdapter[ActionResult] = TypeAdapter(ActionResult)


class PendingResultStore:
    def __init__(self, path: Path) -> None:
        self._store = AtomicJsonStore(path)

    def load(self) -> ActionResult | None:
        payload = self._store.load()
        return None if payload is None else _RESULT_ADAPTER.validate_python(payload, strict=False)

    def save(self, result: ActionResult) -> None:
        self._store.save(result.model_dump(mode="json"))

    def clear(self) -> None:
        self._store.clear()
