from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import httpx2
import pytest
from pydantic import SecretStr

from c2_relay.agent.cli import main
from c2_relay.agent.client import RelayClient
from c2_relay.agent.identity import AgentIdentity, IdentityStore
from c2_relay.core.config import Settings
from c2_relay.models import AgentId


def test_agent_cli_wires_runtime_and_signal_handlers() -> None:
    settings = Settings()
    runtime = MagicMock()

    with (
        patch("c2_relay.agent.cli.get_settings", return_value=settings),
        patch("c2_relay.agent.cli.signal.signal") as register_signal,
        patch("c2_relay.agent.cli.AgentRuntime", return_value=runtime) as runtime_factory,
    ):
        main()

    assert register_signal.call_count == 2
    assert runtime_factory.call_args.kwargs["enrollment_token"] == settings.agent_enrollment_token
    assert runtime_factory.call_args.kwargs["allow_legacy_bootstrap_enrollment"] is False
    runtime.run.assert_called_once()
    handler = register_signal.call_args.args[1]
    handler(2, None)
    assert runtime.run.call_args.args[0].is_set()


@pytest.mark.parametrize("status_code", [401, 403])
@pytest.mark.parametrize("enrolled", [False, True])
def test_agent_cli_exits_with_sanitized_authentication_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], status_code: int, enrolled: bool
) -> None:
    token = "private-enrollment-token-" + "x" * 32
    credential = "private-agent-credential-" + "y" * 32
    state_path = tmp_path / "agent-state.json"
    settings = Settings(
        agent_state_path=state_path,
        agent_result_path=tmp_path / "pending-result.json",
        agent_enrollment_token=SecretStr(token),
        bootstrap_token=None,
    )
    identity = AgentIdentity(
        agent_id=AgentId(UUID("20000000-0000-4000-8000-000000000001")),
        credential=SecretStr(credential),
    )
    if enrolled:
        IdentityStore(state_path).save(identity)
    client = MagicMock(spec=RelayClient)
    request = httpx2.Request("POST", "http://relay.test", headers={"Authorization": credential})
    response = httpx2.Response(
        status_code, request=request, json={"detail": credential, "token": token}
    )
    rejected = httpx2.HTTPStatusError(f"{token} {credential}", request=request, response=response)
    if enrolled:
        client.check_in.side_effect = rejected
    else:
        client.enroll.side_effect = rejected

    with (
        patch("c2_relay.agent.cli.get_settings", return_value=settings),
        patch("c2_relay.agent.cli.signal.signal"),
        patch("c2_relay.agent.cli.RelayClient", return_value=client),
        pytest.raises(SystemExit) as error,
    ):
        main()

    assert error.value.code == 1
    client.close.assert_called_once()
    output = capsys.readouterr()
    assert output.out == ""
    assert f"HTTP {status_code}" in output.err
    assert ("Agent authentication rejected" if enrolled else "Enrollment rejected") in output.err
    assert "Traceback" not in output.err
    assert credential not in output.err
    assert token not in output.err
    if enrolled:
        assert "saved identity and agent status" in output.err
        assert IdentityStore(state_path).load() == identity
    else:
        assert "enrollment credentials and server configuration" in output.err
        assert not state_path.exists()
