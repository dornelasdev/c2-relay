"""Local agent identity storage."""

from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from c2_relay.agent.storage import AtomicJsonStore
from c2_relay.models import AgentId, DomainModel


class AgentIdentity(DomainModel):
    agent_id: AgentId
    credential: SecretStr


class IdentityStore:
    def __init__(self, path: Path) -> None:
        self._store = AtomicJsonStore(path)

    def load(self) -> AgentIdentity | None:
        payload = self._store.load()
        if payload is None:
            return None
        return AgentIdentity(
            agent_id=AgentId(UUID(payload["agent_id"])),
            credential=SecretStr(payload["credential"]),
        )

    def save(self, identity: AgentIdentity) -> None:
        self._store.save(
            {
                "agent_id": str(identity.agent_id),
                "credential": identity.credential.get_secret_value(),
            }
        )
