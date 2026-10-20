import json
import warnings
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from getpass import GetPassWarning
from unittest.mock import MagicMock, patch
from uuid import UUID

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from c2_relay.api.schemas import EnrollmentGrantCreateRequest, EnrollmentGrantIssueResponse
from c2_relay.cli.operators import main
from c2_relay.core.config import Settings
from c2_relay.models import EnrollmentGrant, EnrollmentGrantId, Operator, OperatorId
from c2_relay.services.operators import ProvisionedOperator

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))
CREDENTIAL = "c2o.30000000-0000-4000-8000-000000000001." + "x" * 32
GRANT_ID = EnrollmentGrantId(UUID("50000000-0000-4000-8000-000000000001"))
TOKEN = f"c2g.{GRANT_ID}.{'g' * 32}"
GRANT = EnrollmentGrant(
    id=GRANT_ID,
    issued_by_operator_id=OPERATOR_ID,
    created_at=NOW,
    expires_at=NOW + timedelta(minutes=15),
)


@pytest.fixture
def configured_cli() -> Iterator[tuple[MagicMock, MagicMock]]:
    client = MagicMock()
    client.issue_grant.return_value = EnrollmentGrantIssueResponse(grant=GRANT, token=TOKEN)
    client.revoke_grant.return_value = GRANT.model_copy(
        update={"revoked_at": NOW, "revoked_by_operator_id": OPERATOR_ID}
    )
    with (
        patch(
            "c2_relay.cli.operators.get_settings",
            return_value=Settings(operator_credential=SecretStr(CREDENTIAL)),
        ),
        patch("c2_relay.cli.operators.OperatorClient", return_value=client) as factory,
        patch("c2_relay.cli.operators.create_database_engine") as database,
    ):
        yield client, factory
        database.assert_not_called()


def test_operator_cli_provisions_and_displays_credential_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = MagicMock()
    service = MagicMock()
    service.provision.return_value = ProvisionedOperator(
        operator=Operator(id=OPERATOR_ID, name="Primary Operator", created_at=NOW),
        credential=SecretStr(CREDENTIAL),
    )

    with (
        patch("c2_relay.cli.operators.get_settings", return_value=Settings()),
        patch("c2_relay.cli.operators.create_database_engine", return_value=engine),
        patch("c2_relay.cli.operators.create_session_factory", return_value="factory"),
        patch("c2_relay.cli.operators.OperatorService", return_value=service),
    ):
        main(["create", "--name", "Primary Operator"])

    service.provision.assert_called_once_with("Primary Operator")
    engine.dispose.assert_called_once()
    output = capsys.readouterr().out
    assert f"Operator ID: {OPERATOR_ID}" in output
    assert f"Credential: {CREDENTIAL}" in output
    assert "will not be shown again" in output


def test_operator_cli_issues_grant_as_json_without_printing_operator_credential(
    configured_cli: tuple[MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    client, factory = configured_cli

    main(["grants", "issue"])

    factory.assert_called_once_with("http://127.0.0.1:8000", SecretStr(CREDENTIAL), timeout=10)
    client.issue_grant.assert_called_once_with(EnrollmentGrantCreateRequest())
    client.close.assert_called_once()
    output = capsys.readouterr()
    assert json.loads(output.out)["token"] == TOKEN
    assert json.loads(output.out)["grant"]["id"] == str(GRANT_ID)
    assert CREDENTIAL not in output.out + output.err


def test_operator_cli_overrides_server_and_grant_lifetime(
    configured_cli: tuple[MagicMock, MagicMock],
) -> None:
    client, factory = configured_cli

    main(["grants", "issue", "--expires-in", "120", "--server-url", "https://relay.test"])

    factory.assert_called_once_with("https://relay.test", SecretStr(CREDENTIAL), timeout=10)
    client.issue_grant.assert_called_once_with(EnrollmentGrantCreateRequest(expires_in_seconds=120))


def test_operator_cli_revokes_by_uuid(
    configured_cli: tuple[MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    client, _ = configured_cli

    main(["grants", "revoke", str(GRANT_ID)])

    client.revoke_grant.assert_called_once_with(GRANT_ID)
    client.issue_grant.assert_not_called()
    client.close.assert_called_once()
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == str(GRANT_ID)
    assert payload["revoked_by_operator_id"] == str(OPERATOR_ID)
    assert "token" not in payload


def test_operator_cli_prompts_when_credential_is_unconfigured(
    configured_cli: tuple[MagicMock, MagicMock],
) -> None:
    _, factory = configured_cli
    with (
        patch(
            "c2_relay.cli.operators.get_settings", return_value=Settings(operator_credential=None)
        ),
        patch("c2_relay.cli.operators.getpass", return_value=CREDENTIAL) as prompt,
    ):
        main(["grants", "issue"])
    prompt.assert_called_once_with("Operator credential: ")
    assert factory.call_args.args[1] == SecretStr(CREDENTIAL)


def test_operator_cli_refuses_visible_password_fallback(
    configured_cli: tuple[MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    _, factory = configured_cli

    def visible_fallback(prompt: str) -> str:
        warnings.warn("cannot hide input", GetPassWarning, stacklevel=2)
        raise AssertionError("visible input must not be attempted")

    with (
        patch(
            "c2_relay.cli.operators.get_settings", return_value=Settings(operator_credential=None)
        ),
        patch("c2_relay.cli.operators.getpass", side_effect=visible_fallback),
        pytest.raises(SystemExit) as error,
    ):
        main(["grants", "issue"])
    assert error.value.code == 1
    assert "Hidden credential input unavailable" in capsys.readouterr().err
    factory.assert_not_called()


@pytest.mark.parametrize("failure", [EOFError(), KeyboardInterrupt()])
def test_operator_cli_handles_cancelled_input(
    configured_cli: tuple[MagicMock, MagicMock], failure: BaseException
) -> None:
    _, factory = configured_cli
    with (
        patch(
            "c2_relay.cli.operators.get_settings", return_value=Settings(operator_credential=None)
        ),
        patch("c2_relay.cli.operators.getpass", side_effect=failure),
        pytest.raises(SystemExit) as error,
    ):
        main(["grants", "issue"])
    assert error.value.code == 1
    factory.assert_not_called()


@pytest.mark.parametrize("lifetime", ["59", "86401"])
def test_operator_cli_rejects_invalid_lifetime_before_sending_request(
    configured_cli: tuple[MagicMock, MagicMock], lifetime: str
) -> None:
    _, factory = configured_cli
    with pytest.raises(SystemExit) as error:
        main(["grants", "issue", "--expires-in", lifetime])
    assert error.value.code == 1
    factory.assert_not_called()


def test_operator_cli_rejects_malformed_credential_before_sending_request(
    configured_cli: tuple[MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    _, factory = configured_cli
    secret = "not-an-operator-credential-" + "x" * 32
    with (
        patch(
            "c2_relay.cli.operators.get_settings",
            return_value=Settings(operator_credential=SecretStr(secret)),
        ),
        pytest.raises(SystemExit) as error,
    ):
        main(["grants", "issue"])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert secret not in output.err + output.out
    factory.assert_not_called()


@pytest.mark.parametrize("status", [401, 404, 409, 429, 503])
def test_operator_cli_http_errors_do_not_print_credentials_or_server_body(
    configured_cli: tuple[MagicMock, MagicMock], status: int, capsys: pytest.CaptureFixture[str]
) -> None:
    client, _ = configured_cli
    request = httpx2.Request("POST", "http://relay.test/api/v1/enrollment-grants")
    client.issue_grant.side_effect = httpx2.HTTPStatusError(
        CREDENTIAL,
        request=request,
        response=httpx2.Response(status, json={"detail": TOKEN}, request=request),
    )

    with pytest.raises(SystemExit) as error:
        main(["grants", "issue"])

    assert error.value.code == 1
    client.close.assert_called_once()
    output = capsys.readouterr()
    assert f"HTTP {status}" in output.err
    assert CREDENTIAL not in output.err + output.out
    assert TOKEN not in output.err + output.out


def test_operator_cli_connection_errors_are_sanitized(
    configured_cli: tuple[MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    client, _ = configured_cli
    client.issue_grant.side_effect = httpx2.ConnectError(CREDENTIAL)
    with pytest.raises(SystemExit) as error:
        main(["grants", "issue"])
    assert error.value.code == 1
    client.close.assert_called_once()
    output = capsys.readouterr()
    assert "API connection failed" in output.err
    assert CREDENTIAL not in output.err + output.out


def test_operator_cli_rejects_invalid_response_without_echoing_it(
    configured_cli: tuple[MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    client, _ = configured_cli
    with pytest.raises(ValidationError) as validation:
        EnrollmentGrantIssueResponse.model_validate({"token": TOKEN})
    client.issue_grant.side_effect = validation.value
    with pytest.raises(SystemExit):
        main(["grants", "issue"])
    client.close.assert_called_once()
    output = capsys.readouterr()
    assert "Invalid grant data" in output.err
    assert TOKEN not in output.err + output.out


def test_operator_cli_configuration_errors_are_sanitized(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(ValidationError) as validation:
        Settings(operator_request_timeout=0)
    with (
        patch("c2_relay.cli.operators.get_settings", side_effect=validation.value),
        pytest.raises(SystemExit) as error,
    ):
        main(["grants", "issue"])
    assert error.value.code == 2
    assert "Invalid configuration" in capsys.readouterr().err


def test_operator_cli_invalid_url_error_is_sanitized(
    configured_cli: tuple[MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    _, factory = configured_cli
    factory.side_effect = httpx2.InvalidURL(CREDENTIAL)
    with pytest.raises(SystemExit) as error:
        main(["grants", "issue"])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert CREDENTIAL not in output.err + output.out
