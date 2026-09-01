from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from c2_relay.agent.results import PendingResultStore
from c2_relay.models import ActionSuccess, AgentId, HostnameOutput, TaskId

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)


def result() -> ActionSuccess:
    return ActionSuccess(
        task_id=TaskId(UUID("10000000-0000-4000-8000-000000000001")),
        agent_id=AgentId(UUID("20000000-0000-4000-8000-000000000001")),
        completed_at=NOW,
        output=HostnameOutput(hostname="relay-host"),
    )


def test_pending_result_store_round_trip_and_clear(tmp_path: Path) -> None:
    path = tmp_path / "pending-result.json"
    store = PendingResultStore(path)

    assert store.load() is None
    store.save(result())
    assert store.load() == result()
    assert path.stat().st_mode & 0o777 == 0o600

    store.clear()
    store.clear()
    assert store.load() is None
