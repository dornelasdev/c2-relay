from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock
from uuid import UUID

import httpx2
import pytest
from pydantic import SecretStr

from c2_relay.agent.actions import ActionRegistry
from c2_relay.agent.client import RelayClient
from c2_relay.agent.identity import AgentIdentity, IdentityStore
from c2_relay.agent.results import PendingResultStore
from c2_relay.agent.runtime import AgentRuntime
from c2_relay.api.schemas import EnrollmentResponse
from c2_relay.models import (
    ActionSuccess,
    AgentId,
    HostnameAction,
    HostnameOutput,
    Task,
    TaskId,
    TaskStatus,
)

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))
IDENTITY = AgentIdentity(agent_id=AGENT_ID, credential=SecretStr("credential"))


def claimed_task() -> Task:
    return Task(
        id=TaskId(UUID("10000000-0000-4000-8000-000000000001")),
        agent_id=AGENT_ID,
        action=HostnameAction(),
        status=TaskStatus.CLAIMED,
        created_at=NOW,
        updated_at=NOW,
        lease_expires_at=NOW + timedelta(seconds=30),
    )


def completed_result(agent_id: AgentId = AGENT_ID) -> ActionSuccess:
    return ActionSuccess(
        task_id=claimed_task().id,
        agent_id=agent_id,
        completed_at=NOW,
        output=HostnameOutput(hostname="relay-host"),
    )


def runtime(
    client: RelayClient,
    store: IdentityStore,
    result_store: PendingResultStore | None = None,
) -> AgentRuntime:
    if result_store is None:
        result_store = MagicMock(spec=PendingResultStore)
        result_store.load.return_value = None
    return AgentRuntime(
        client,
        store,
        result_store,
        ActionRegistry(),
        bootstrap_token=SecretStr("bootstrap"),
        poll_interval=1,
        max_backoff=2,
        clock=lambda: NOW,
        jitter=lambda: 0,
    )


def test_run_once_checks_in_and_handles_an_empty_queue() -> None:
    client = MagicMock(spec=RelayClient)
    client.next_task.return_value = None

    runtime(client, MagicMock(spec=IdentityStore)).run_once(IDENTITY)

    client.check_in.assert_called_once()
    client.submit_result.assert_not_called()


def test_run_once_executes_and_submits_a_task() -> None:
    client = MagicMock(spec=RelayClient)
    client.next_task.return_value = claimed_task()

    runtime(client, MagicMock(spec=IdentityStore)).run_once(IDENTITY)

    client.submit_result.assert_called_once()


def test_runtime_persists_and_retries_result_before_polling(tmp_path: Path) -> None:
    store = PendingResultStore(tmp_path / "pending-result.json")
    client = MagicMock(spec=RelayClient)
    client.next_task.return_value = claimed_task()
    request = httpx2.Request("POST", "http://relay.test/api/v1/agents/id/results")
    client.submit_result.side_effect = httpx2.ConnectError("offline", request=request)

    with pytest.raises(httpx2.ConnectError):
        runtime(client, MagicMock(spec=IdentityStore), store).run_once(IDENTITY)

    stored = store.load()
    assert stored is not None
    assert stored.task_id == claimed_task().id
    assert stored.agent_id == AGENT_ID

    client.reset_mock()
    client.submit_result.side_effect = None
    client.next_task.return_value = None
    runtime(client, MagicMock(spec=IdentityStore), store).run_once(IDENTITY)

    assert store.load() is None
    assert [call[0] for call in client.method_calls[:3]] == [
        "submit_result",
        "check_in",
        "next_task",
    ]


@pytest.mark.parametrize("status_code", [404, 409])
def test_runtime_discards_a_stale_pending_result(tmp_path: Path, status_code: int) -> None:
    store = PendingResultStore(tmp_path / "pending-result.json")
    store.save(completed_result())
    client = MagicMock(spec=RelayClient)
    client.next_task.return_value = None
    request = httpx2.Request("POST", "http://relay.test/api/v1/agents/id/results")
    response = httpx2.Response(status_code, request=request)
    client.submit_result.side_effect = httpx2.HTTPStatusError(
        "stale result",
        request=request,
        response=response,
    )

    runtime(client, MagicMock(spec=IdentityStore), store).run_once(IDENTITY)

    assert store.load() is None
    client.check_in.assert_called_once()


def test_runtime_preserves_pending_result_after_retryable_server_error(tmp_path: Path) -> None:
    store = PendingResultStore(tmp_path / "pending-result.json")
    store.save(completed_result())
    client = MagicMock(spec=RelayClient)
    request = httpx2.Request("POST", "http://relay.test/api/v1/agents/id/results")
    response = httpx2.Response(503, request=request)
    client.submit_result.side_effect = httpx2.HTTPStatusError(
        "server unavailable",
        request=request,
        response=response,
    )

    with pytest.raises(httpx2.HTTPStatusError):
        runtime(client, MagicMock(spec=IdentityStore), store).run_once(IDENTITY)

    assert store.load() == completed_result()


def test_runtime_rejects_pending_result_for_another_identity(tmp_path: Path) -> None:
    store = PendingResultStore(tmp_path / "pending-result.json")
    other_id = AgentId(UUID("20000000-0000-4000-8000-000000000002"))
    store.save(completed_result(other_id))
    client = MagicMock(spec=RelayClient)

    with pytest.raises(RuntimeError, match="different agent identity"):
        runtime(client, MagicMock(spec=IdentityStore), store).run_once(IDENTITY)

    client.submit_result.assert_not_called()


def test_runtime_enrolls_saves_and_closes() -> None:
    client = MagicMock(spec=RelayClient)
    client.enroll.return_value = EnrollmentResponse(
        agent_id=AGENT_ID, credential="credential-" + "x" * 32
    )
    client.next_task.return_value = None
    store = MagicMock(spec=IdentityStore)
    store.load.return_value = None
    stop = MagicMock(spec=Event)
    stop.is_set.side_effect = [False, True]

    runtime(client, store).run(stop)

    client.enroll.assert_called_once()
    store.save.assert_called_once()
    client.close.assert_called_once()


def test_runtime_backs_off_after_http_errors() -> None:
    client = MagicMock(spec=RelayClient)
    request = httpx2.Request("GET", "http://relay.test")
    client.check_in.side_effect = httpx2.ConnectError("offline", request=request)
    store = MagicMock(spec=IdentityStore)
    store.load.return_value = IDENTITY
    stop = MagicMock(spec=Event)
    stop.is_set.side_effect = [False, False, True]

    runtime(client, store).run(stop)

    assert stop.wait.call_args_list[0].args == (1.0,)
    assert stop.wait.call_args_list[1].args == (2.0,)
    client.close.assert_called_once()


def test_runtime_retries_initial_enrollment_with_backoff() -> None:
    client = MagicMock(spec=RelayClient)
    request = httpx2.Request("POST", "http://relay.test/api/v1/agents/enroll")
    client.enroll.side_effect = [
        httpx2.ConnectError("offline", request=request),
        EnrollmentResponse(agent_id=AGENT_ID, credential="credential-" + "x" * 32),
    ]
    client.next_task.return_value = None
    store = MagicMock(spec=IdentityStore)
    store.load.return_value = None
    stop = MagicMock(spec=Event)
    stop.is_set.side_effect = [False, False, True]

    runtime(client, store).run(stop)

    assert client.enroll.call_count == 2
    store.save.assert_called_once()
    assert stop.wait.call_args_list[0].args == (1.0,)
    client.close.assert_called_once()


@pytest.mark.parametrize("status_code", [401, 403])
def test_runtime_stops_after_permanent_authentication_failure(status_code: int) -> None:
    client = MagicMock(spec=RelayClient)
    request = httpx2.Request("POST", "http://relay.test/api/v1/agents/id/check-ins")
    response = httpx2.Response(status_code, request=request)
    client.check_in.side_effect = httpx2.HTTPStatusError(
        "authentication rejected",
        request=request,
        response=response,
    )
    store = MagicMock(spec=IdentityStore)
    store.load.return_value = IDENTITY
    stop = MagicMock(spec=Event)
    stop.is_set.return_value = False

    runtime(client, store).run(stop)

    stop.wait.assert_not_called()
    client.check_in.assert_called_once()
    client.close.assert_called_once()


def test_runtime_backs_off_after_retryable_server_status() -> None:
    client = MagicMock(spec=RelayClient)
    request = httpx2.Request("POST", "http://relay.test/api/v1/agents/id/check-ins")
    response = httpx2.Response(503, request=request)
    client.check_in.side_effect = httpx2.HTTPStatusError(
        "server unavailable",
        request=request,
        response=response,
    )
    store = MagicMock(spec=IdentityStore)
    store.load.return_value = IDENTITY
    stop = MagicMock(spec=Event)
    stop.is_set.side_effect = [False, True]

    runtime(client, store).run(stop)

    stop.wait.assert_called_once_with(1.0)
    client.close.assert_called_once()


def test_runtime_requires_bootstrap_token_for_first_enrollment() -> None:
    client = MagicMock(spec=RelayClient)
    store = MagicMock(spec=IdentityStore)
    store.load.return_value = None
    value = AgentRuntime(
        client,
        store,
        MagicMock(spec=PendingResultStore),
        ActionRegistry(),
        bootstrap_token=None,
        poll_interval=1,
        max_backoff=2,
    )

    with pytest.raises(RuntimeError, match="no bootstrap token"):
        value.run(Event())

    client.close.assert_called_once()
