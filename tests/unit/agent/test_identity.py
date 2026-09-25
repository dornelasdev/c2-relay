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
    path.chmod(0o600)

    with pytest.raises(ValueError, match="must contain a JSON object"):
        IdentityStore(path).load()


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o400])
def test_identity_store_rejects_incorrect_existing_mode(tmp_path: Path, mode: int) -> None:
    path = tmp_path / "identity.json"
    IdentityStore(path).save(identity())
    path.chmod(mode)

    with pytest.raises(PermissionError, match="mode 0600"):
        IdentityStore(path).load()


def test_identity_store_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "identity.json"
    IdentityStore(target).save(identity())
    link = tmp_path / "identity-link.json"
    link.symlink_to(target)

    with pytest.raises(PermissionError, match="symbolic link"):
        IdentityStore(link).load()


def test_identity_store_preserves_other_open_errors(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"

    with (
        patch("c2_relay.agent.storage.os.open", side_effect=PermissionError("access denied")),
        pytest.raises(PermissionError, match="access denied"),
    ):
        IdentityStore(path).load()


def test_identity_store_rejects_non_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    path.mkdir()

    with pytest.raises(ValueError, match="regular file"):
        IdentityStore(path).load()


def test_identity_store_rejects_another_owner(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    IdentityStore(path).save(identity())

    with (
        patch("c2_relay.agent.storage.os.getuid", return_value=path.stat().st_uid + 1),
        pytest.raises(PermissionError, match="owned by the current user"),
    ):
        IdentityStore(path).load()
