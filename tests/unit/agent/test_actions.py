from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import pytest

from c2_relay.agent.actions import ActionHandler, ActionRegistry
from c2_relay.models import (
    ActionFailure,
    ActionKind,
    ActionSuccess,
    AgentId,
    CurrentUserAction,
    CurrentUserOutput,
    HostnameAction,
    HostnameOutput,
    OperatingSystemAction,
    OperatingSystemOutput,
    Task,
    TaskId,
)

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))


def task(action: HostnameAction | CurrentUserAction | OperatingSystemAction) -> Task:
    return Task(
        id=TaskId(UUID("10000000-0000-4000-8000-000000000001")),
        agent_id=AGENT_ID,
        action=action,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("action", "handler", "expected"),
    [
        (HostnameAction(), lambda: HostnameOutput(hostname="host"), HostnameOutput),
        (CurrentUserAction(), lambda: CurrentUserOutput(username="user"), CurrentUserOutput),
        (
            OperatingSystemAction(),
            lambda: OperatingSystemOutput(
                system="Linux", release="1", version="build", machine="x86_64"
            ),
            OperatingSystemOutput,
        ),
    ],
)
def test_registry_executes_allowlisted_actions(
    action: HostnameAction | CurrentUserAction | OperatingSystemAction,
    handler: ActionHandler,
    expected: type[object],
) -> None:
    registry = ActionRegistry({action.kind: handler})

    result = registry.execute(task(action), AGENT_ID, clock=lambda: NOW)

    assert isinstance(result, ActionSuccess)
    assert isinstance(result.output, expected)


@pytest.mark.parametrize(
    "handler",
    [
        lambda: (_ for _ in ()).throw(RuntimeError("collection failed")),
        lambda: HostnameOutput(hostname="host"),
    ],
)
def test_registry_returns_a_structured_failure(handler: ActionHandler) -> None:
    kind = ActionKind.HOSTNAME if "throw" in repr(handler) else ActionKind.CURRENT_USER
    action = HostnameAction() if kind is ActionKind.HOSTNAME else CurrentUserAction()
    result = ActionRegistry({kind: handler}).execute(task(action), AGENT_ID, clock=lambda: NOW)

    assert isinstance(result, ActionFailure)
    assert result.error.code == "action_failed"


@pytest.mark.parametrize("fails", [False, True])
def test_completion_time_is_captured_after_handler(fails: bool) -> None:
    calls: list[str] = []

    def handler() -> HostnameOutput:
        calls.append("handler")
        if fails:
            raise RuntimeError("collection failed")
        return HostnameOutput(hostname="host")

    def clock() -> datetime:
        calls.append("clock")
        return NOW + timedelta(seconds=5)

    result = ActionRegistry({ActionKind.HOSTNAME: handler}).execute(
        task(HostnameAction()), AGENT_ID, clock=clock
    )

    assert calls == ["handler", "clock"]
    assert result.completed_at == NOW + timedelta(seconds=5)
    assert isinstance(result, ActionFailure if fails else ActionSuccess)


def test_default_registry_collects_user_and_operating_system() -> None:
    registry = ActionRegistry()
    with (
        patch("c2_relay.agent.actions.getpass.getuser", return_value="user"),
        patch("c2_relay.agent.actions.platform.system", return_value="Linux"),
        patch("c2_relay.agent.actions.platform.release", return_value="1"),
        patch("c2_relay.agent.actions.platform.version", return_value="build"),
        patch("c2_relay.agent.actions.platform.machine", return_value="x86_64"),
    ):
        user_result = registry.execute(task(CurrentUserAction()), AGENT_ID, clock=lambda: NOW)
        os_result = registry.execute(task(OperatingSystemAction()), AGENT_ID, clock=lambda: NOW)

    assert isinstance(user_result, ActionSuccess)
    assert user_result.output == CurrentUserOutput(username="user")
    assert isinstance(os_result, ActionSuccess)
    assert os_result.output == OperatingSystemOutput(
        system="Linux", release="1", version="build", machine="x86_64"
    )
