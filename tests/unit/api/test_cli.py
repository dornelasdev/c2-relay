from unittest.mock import patch

from pydantic import SecretStr

from c2_relay.api.cli import main
from c2_relay.core.config import Settings


def test_cli_runs_uvicorn_with_configured_local_binding() -> None:
    settings = Settings(
        bootstrap_token=SecretStr("b" * 32),
        server_host="127.0.0.1",
        server_port=9000,
        log_level="WARNING",
    )

    with (
        patch("c2_relay.api.cli.get_settings", return_value=settings),
        patch("c2_relay.api.cli.create_app", return_value="app") as create,
        patch("c2_relay.api.cli.uvicorn.run") as run,
    ):
        main()

    create.assert_called_once_with(settings)
    run.assert_called_once_with("app", host="127.0.0.1", port=9000, log_level="warning")
