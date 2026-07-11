"""Agent command-line entry point."""

import signal
from threading import Event

from c2_relay.agent.actions import ActionRegistry
from c2_relay.agent.client import RelayClient
from c2_relay.agent.identity import IdentityStore
from c2_relay.agent.runtime import AgentRuntime
from c2_relay.core.config import get_settings


def main() -> None:
    settings = get_settings()
    stop = Event()

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    runtime = AgentRuntime(
        RelayClient(settings.agent_server_url, timeout=settings.agent_request_timeout),
        IdentityStore(settings.agent_state_path),
        ActionRegistry(),
        bootstrap_token=settings.bootstrap_token,
        poll_interval=settings.agent_poll_interval,
        max_backoff=settings.agent_max_backoff,
    )
    runtime.run(stop)
