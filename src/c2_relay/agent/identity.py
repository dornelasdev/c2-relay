"""Local agent identity storage."""

import json
import os
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from c2_relay.models import AgentId, DomainModel


class AgentIdentity(DomainModel):
    agent_id: AgentId
    credential: SecretStr


class IdentityStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> AgentIdentity | None:
        if not self._path.exists():
            return None
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        return AgentIdentity(
            agent_id=AgentId(UUID(payload["agent_id"])),
            credential=SecretStr(payload["credential"]),
        )

    def save(self, identity: AgentIdentity) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        payload = {
            "agent_id": str(identity.agent_id),
            "credential": identity.credential.get_secret_value(),
        }
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
                stream.write("\n")
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
