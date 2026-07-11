from datetime import UTC, datetime
from threading import Event
from unittest.mock import MagicMock
from uuid import UUID

import httpx2
import pytest
from pydantic import SecretStr

from c2_relay.agent.actions import ActionRegistry
from c2_relay.agent.client import RelayClient
from c2_relay.agent.identity import AgentIdentity, IdentityStore
from c2_relay.agent.runtime import AgentRuntime
from c2_relay.api.schemas import EnrollmentResponse
from c2_relay.models import AgentId, HostnameAction, Task, TaskId

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))
IDENTITY = AgentIdentity(agent_id=AGENT_ID, credential=SecretStr("credential"))


def runtime(client: RelayClient, store: IdentityStore) -> AgentRuntime:
    return AgentRuntime(
        client,
        store,
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
    client.next_task.return_value = Task(
        id=TaskId(UUID("10000000-0000-4000-8000-000000000001")),
        agent_id=AGENT_ID,
        action=HostnameAction(),
        created_at=NOW,
        updated_at=NOW,
    )

    runtime(client, MagicMock(spec=IdentityStore)).run_once(IDENTITY)

    client.submit_result.assert_called_once()


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


def test_runtime_requires_bootstrap_token_for_first_enrollment() -> None:
    client = MagicMock(spec=RelayClient)
    store = MagicMock(spec=IdentityStore)
    store.load.return_value = None
    value = AgentRuntime(
        client,
        store,
        ActionRegistry(),
        bootstrap_token=None,
        poll_interval=1,
        max_backoff=2,
    )

    with pytest.raises(RuntimeError, match="no bootstrap token"):
        value.run(Event())

    client.close.assert_called_once()
