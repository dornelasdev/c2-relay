import json
import stat
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import SecretStr

from c2_relay.agent.identity import AgentIdentity, IdentityStore
from c2_relay.models import AgentId


def identity() -> AgentIdentity:
    return AgentIdentity(
        agent_id=AgentId(UUID("20000000-0000-4000-8000-000000000001")),
        credential=SecretStr("secret-credential"),
    )


def test_identity_store_round_trip_uses_restrictive_permissions(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "identity.json"
    store = IdentityStore(path)

    assert store.load() is None
    store.save(identity())

    assert store.load() == identity()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "secret-credential" in path.read_text()
    assert not list(path.parent.glob(".identity.json.*.tmp"))


def test_identity_store_cleans_up_a_failed_atomic_write(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    with patch.object(json, "dump", side_effect=OSError("write failed")), pytest.raises(OSError):
        IdentityStore(path).save(identity())

    assert not path.exists()
    assert not list(path.parent.glob(".identity.json.*.tmp"))


def test_identity_store_rejects_non_object_state(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a JSON object"):
        IdentityStore(path).load()
