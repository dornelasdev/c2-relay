"""Owner-only atomic JSON storage for local agent state."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class AtomicJsonStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Any] | None:
        if not self._path.exists():
            return None
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("local state must contain a JSON object")
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
                stream.write("\n")
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
