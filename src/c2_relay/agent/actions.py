"""Fixed, read-only action execution registry."""

import getpass
import platform
from collections.abc import Callable
from datetime import datetime

from c2_relay.models import (
    ActionFailure,
    ActionKind,
    ActionResult,
    ActionSuccess,
    AgentId,
    CurrentUserOutput,
    ErrorDetail,
    HostnameOutput,
    OperatingSystemOutput,
    Task,
)

ActionHandler = Callable[[], HostnameOutput | CurrentUserOutput | OperatingSystemOutput]


def _hostname() -> HostnameOutput:
    return HostnameOutput(hostname=platform.node())


def _current_user() -> CurrentUserOutput:
    return CurrentUserOutput(username=getpass.getuser())


def _operating_system() -> OperatingSystemOutput:
    return OperatingSystemOutput(
        system=platform.system(),
        release=platform.release(),
        version=platform.version(),
        machine=platform.machine(),
    )


class ActionRegistry:
    def __init__(self, handlers: dict[ActionKind, ActionHandler] | None = None) -> None:
        self._handlers = handlers or {
            ActionKind.HOSTNAME: _hostname,
            ActionKind.CURRENT_USER: _current_user,
            ActionKind.OPERATING_SYSTEM: _operating_system,
        }

    def execute(self, task: Task, agent_id: AgentId, *, completed_at: datetime) -> ActionResult:
        try:
            output = self._handlers[task.action.kind]()
            if output.kind != task.action.kind:
                msg = "action handler returned an incompatible output"
                raise ValueError(msg)
            return ActionSuccess(
                task_id=task.id,
                agent_id=agent_id,
                completed_at=completed_at,
                output=output,
            )
        except Exception as exc:
            return ActionFailure(
                task_id=task.id,
                agent_id=agent_id,
                completed_at=completed_at,
                error=ErrorDetail(
                    code="action_failed",
                    message=str(exc) or type(exc).__name__,
                    retryable=False,
                ),
            )
