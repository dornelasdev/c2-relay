import json
import stat
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from c2_relay.cli.context import OperatorContext, OperatorContextError, OperatorContextStore
from c2_relay.models import AgentId, OperatorId, TaskId

OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))
TASK_ID = TaskId(UUID("10000000-0000-4000-8000-000000000001"))
ORIGIN = "http://relay.test"


def selected_context() -> OperatorContext:
    return OperatorContext(
        server_url=ORIGIN, operator_id=OPERATOR_ID, selected_agent_id=AGENT_ID, last_task_id=TASK_ID
    )


def test_context_round_trip_contains_only_references_and_uses_owner_permissions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "context.json"
    store = OperatorContextStore(path)
    empty = store.load("HTTP://RELAY.TEST:80/", OPERATOR_ID)
    assert empty.server_url == ORIGIN
    assert empty.selected_agent_id is None
    assert empty.last_task_id is None
    assert not path.exists()

    store.save(selected_context())

    assert store.load("HTTP://RELAY.TEST:80/", OPERATOR_ID) == selected_context()
    assert set(json.loads(path.read_text())) == {
        "server_url",
        "operator_id",
        "selected_agent_id",
        "last_task_id",
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob(".context.json.*.tmp"))


@pytest.mark.parametrize(
    "server_url, operator_id",
    [("https://other.test", OPERATOR_ID), (ORIGIN, OperatorId(UUID(int=99)))],
)
def test_other_servers_or_operators_cannot_reuse_context_selections(
    tmp_path: Path, server_url: str, operator_id: OperatorId
) -> None:
    path = tmp_path / "context.json"
    store = OperatorContextStore(path)
    store.save(selected_context())
    original = path.read_bytes()

    context = store.load(server_url, operator_id)

    assert context.server_url == server_url
    assert context.operator_id == operator_id
    assert context.selected_agent_id is None
    assert context.last_task_id is None
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        "not-json",
        "{}",
        json.dumps({"server_url": "http://relay.test:invalid", "operator_id": str(OPERATOR_ID)}),
        json.dumps({"server_url": ORIGIN, "operator_id": "private-invalid-identifier"}),
        json.dumps(
            {"server_url": ORIGIN, "operator_id": str(OPERATOR_ID), "credential": "private-secret"}
        ),
        json.dumps(
            {"server_url": "http://user:private-secret@relay.test", "operator_id": str(OPERATOR_ID)}
        ),
    ],
)
def test_invalid_context_is_not_echoed_or_overwritten(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "context.json"
    path.write_text(payload)
    path.chmod(0o600)
    store = OperatorContextStore(path)

    for operation in (
        lambda: store.load(ORIGIN, OPERATOR_ID),
        lambda: store.save(selected_context()),
    ):
        with pytest.raises(OperatorContextError, match="Cannot read operator context") as error:
            operation()
        assert "private-" not in str(error.value)
    assert path.read_text() == payload


def test_context_rejects_insecure_files_and_symlinks(tmp_path: Path) -> None:
    path = tmp_path / "context.json"
    store = OperatorContextStore(path)
    store.save(selected_context())
    path.chmod(0o644)
    with pytest.raises(OperatorContextError, match="owner-only permissions"):
        store.load(ORIGIN, OPERATOR_ID)
    path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OperatorContextError):
        OperatorContextStore(link).save(selected_context())
    assert link.is_symlink()
    assert store.load(ORIGIN, OPERATOR_ID) == selected_context()


def test_failed_context_write_preserves_the_previous_selection(tmp_path: Path) -> None:
    path = tmp_path / "context.json"
    store = OperatorContextStore(path)
    store.save(selected_context())
    original = path.read_bytes()
    with (
        patch("c2_relay.core.storage.os.replace", side_effect=PermissionError("private-path")),
        pytest.raises(OperatorContextError, match="Cannot save operator context") as error,
    ):
        store.save(OperatorContext(server_url=ORIGIN, operator_id=OPERATOR_ID))
    assert "private-path" not in str(error.value)
    assert path.read_bytes() == original
    assert not list(path.parent.glob(".context.json.*.tmp"))
