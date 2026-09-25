"""Owner-only atomic JSON storage for local agent state."""

import errno
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any


class AtomicJsonStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Any] | None:
        try:
            descriptor = os.open(self._path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError as error:
            if error.errno == errno.ELOOP:
                message = f"local state must not be a symbolic link: {self._path}"
                raise PermissionError(message) from error
            raise
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(f"local state must be a regular file: {self._path}")
            if stat.S_IMODE(file_stat.st_mode) != 0o600:
                raise PermissionError(f"local state file must have mode 0600: {self._path}")
            if file_stat.st_uid != os.getuid():
                message = f"local state file must be owned by the current user: {self._path}"
                raise PermissionError(message)
        except BaseException:
            os.close(descriptor)
            raise
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
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
