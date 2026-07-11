from unittest.mock import MagicMock, patch

from c2_relay.agent.cli import main
from c2_relay.core.config import Settings


def test_agent_cli_wires_runtime_and_signal_handlers() -> None:
    settings = Settings()
    runtime = MagicMock()

    with (
        patch("c2_relay.agent.cli.get_settings", return_value=settings),
        patch("c2_relay.agent.cli.signal.signal") as register_signal,
        patch("c2_relay.agent.cli.AgentRuntime", return_value=runtime),
    ):
        main()

    assert register_signal.call_count == 2
    runtime.run.assert_called_once()
    handler = register_signal.call_args.args[1]
    handler(2, None)
    assert runtime.run.call_args.args[0].is_set()
