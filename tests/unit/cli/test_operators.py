import json
import warnings
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from getpass import GetPassWarning
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from c2_relay.api.schemas import (
    AgentListResponse,
    AuditEventListResponse,
    EnrollmentGrantCreateRequest,
    EnrollmentGrantIssueResponse,
    StoredResultResponse,
    TaskListResponse,
)
from c2_relay.cli.context import OperatorContext, OperatorContextStore
from c2_relay.cli.operators import main
from c2_relay.core.config import Settings
from c2_relay.models import (
    ActionSuccess,
    AgentId,
    AgentMetadata,
    AgentStatus,
    AuditEventType,
    EnrollmentGrant,
    EnrollmentGrantId,
    HostnameAction,
    HostnameOutput,
    Operator,
    OperatorId,
    RegisteredAgent,
    StoredActionResult,
    Task,
    TaskId,
    TaskStatus,
)
from c2_relay.services.operators import ProvisionedOperator

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))
CREDENTIAL = "c2o.30000000-0000-4000-8000-000000000001." + "x" * 32
GRANT_ID = EnrollmentGrantId(UUID("50000000-0000-4000-8000-000000000001"))
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))
TASK_ID = TaskId(UUID("10000000-0000-4000-8000-000000000001"))
TOKEN = f"c2g.{GRANT_ID}.{'g' * 32}"
GRANT = EnrollmentGrant(
    id=GRANT_ID,
    issued_by_operator_id=OPERATOR_ID,
    created_at=NOW,
    expires_at=NOW + timedelta(minutes=15),
)


def save_selection(tmp_path: Path) -> OperatorContextStore:
    store = OperatorContextStore(tmp_path / "operator-context.json")
    store.save(
        OperatorContext(
            server_url="http://127.0.0.1:8000",
            operator_id=OPERATOR_ID,
            selected_agent_id=AGENT_ID,
            last_task_id=TASK_ID,
        )
    )
    return store


def test_select_agent_and_show_or_clear_context_without_network(
    configured_cli: tuple[MagicMock, MagicMock],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, factory = configured_cli
    main(["agents", "use", str(AGENT_ID)])
    context = json.loads(capsys.readouterr().out)
    assert context["selected_agent_id"] == str(AGENT_ID)
    assert context["last_task_id"] is None
    assert CREDENTIAL not in (tmp_path / "operator-context.json").read_text()
    client.get_agent.assert_called_once_with(AGENT_ID)
    client.close.assert_called_once()
    factory.reset_mock()
    main(["context", "show"])
    assert json.loads(capsys.readouterr().out) == context
    main(["context", "clear"])
    cleared = json.loads(capsys.readouterr().out)
    assert cleared["selected_agent_id"] is cleared["last_task_id"] is None
    factory.assert_not_called()


@pytest.mark.parametrize("same_agent", [True, False])
def test_selecting_another_agent_clears_the_last_task(
    configured_cli: tuple[MagicMock, MagicMock],
    tmp_path: Path,
    same_agent: bool,
) -> None:
    client, _ = configured_cli
    store = save_selection(tmp_path)
    selected_id = AGENT_ID if same_agent else AgentId(UUID(int=42))
    client.get_agent.return_value = client.get_agent.return_value.model_copy(
        update={"id": selected_id}
    )
    main(["agents", "use", str(selected_id)])
    context = store.load("http://127.0.0.1:8000", OPERATOR_ID)
    assert context.selected_agent_id == selected_id
    assert context.last_task_id == (TASK_ID if same_agent else None)


@pytest.mark.parametrize("failure", ["disabled", "different", "missing"])
def test_unusable_agent_does_not_replace_selection(
    configured_cli: tuple[MagicMock, MagicMock],
    tmp_path: Path,
    failure: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, _ = configured_cli
    save_selection(tmp_path)
    path = tmp_path / "operator-context.json"
    previous = path.read_bytes()
    if failure == "missing":
        response = httpx2.Response(404, request=httpx2.Request("GET", "http://relay.test"))
        client.get_agent.side_effect = httpx2.HTTPStatusError(
            "private-secret", request=response.request, response=response
        )
    else:
        updates = (
            {"status": AgentStatus.DISABLED}
            if failure == "disabled"
            else {"id": AgentId(UUID(int=42))}
        )
        client.get_agent.return_value = client.get_agent.return_value.model_copy(update=updates)
    with pytest.raises(SystemExit) as error:
        main(["agents", "use", str(AGENT_ID)])
    assert error.value.code == 1
    assert path.read_bytes() == previous
    assert "private-secret" not in capsys.readouterr().err
    client.close.assert_called_once()


@pytest.mark.parametrize(
    "flags, expected",
    [([], AGENT_ID), (["--agent-id", str(UUID(int=42))], UUID(int=42)), (["--all-agents"], None)],
)
def test_task_list_defaults_to_selection_but_explicit_filters_win(
    configured_cli: tuple[MagicMock, MagicMock],
    tmp_path: Path,
    flags: list[str],
    expected: UUID | None,
) -> None:
    client, _ = configured_cli
    save_selection(tmp_path)
    main(["tasks", "list", *flags])
    client.list_tasks.assert_called_once_with(agent_id=expected, status=None, limit=50, offset=0)


def test_task_list_rejects_conflicting_agent_filters(
    configured_cli: tuple[MagicMock, MagicMock],
) -> None:
    _, factory = configured_cli
    with pytest.raises(SystemExit) as error:
        main(["tasks", "list", "--agent-id", str(AGENT_ID), "--all-agents"])
    assert error.value.code == 2
    factory.assert_not_called()


@pytest.mark.parametrize("command", ["show", "result"])
@pytest.mark.parametrize("explicit", [True, False])
def test_task_reads_remember_the_id_without_retargeting_the_selected_agent(
    configured_cli: tuple[MagicMock, MagicMock],
    tmp_path: Path,
    command: str,
    explicit: bool,
) -> None:
    client, _ = configured_cli
    store = save_selection(tmp_path)
    context = store.load("http://127.0.0.1:8000", OPERATOR_ID)
    store.save(
        context.model_copy(update={"last_task_id": TaskId(UUID(int=42)) if explicit else TASK_ID})
    )
    main(["tasks", command, *([str(TASK_ID)] if explicit else [])])
    method = client.get_task if command == "show" else client.get_task_result
    method.assert_called_once_with(task_id=TASK_ID)
    saved = store.load("http://127.0.0.1:8000", OPERATOR_ID)
    assert saved.last_task_id == TASK_ID
    assert saved.selected_agent_id == AGENT_ID


@pytest.mark.parametrize("command", ["show", "result"])
def test_task_reads_reject_a_mismatched_response_without_saving(
    configured_cli: tuple[MagicMock, MagicMock],
    tmp_path: Path,
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, _ = configured_cli
    save_selection(tmp_path)
    path = tmp_path / "operator-context.json"
    previous = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        main(["tasks", command, str(UUID(int=42))])
    assert error.value.code == 1
    assert "different task" in capsys.readouterr().err
    assert path.read_bytes() == previous
    client.close.assert_called_once()


@pytest.mark.parametrize("command", ["show", "result"])
def test_task_id_is_required_when_no_last_task_exists(
    configured_cli: tuple[MagicMock, MagicMock],
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, _ = configured_cli
    with pytest.raises(SystemExit) as error:
        main(["tasks", command])
    assert error.value.code == 1
    assert "No last task" in capsys.readouterr().err
    client.get_task.assert_not_called()
    client.get_task_result.assert_not_called()
    client.close.assert_called_once()


@pytest.mark.parametrize("scope", ["server", "operator"])
def test_other_scopes_do_not_reuse_cli_selections(
    configured_cli: tuple[MagicMock, MagicMock],
    tmp_path: Path,
    scope: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, _ = configured_cli
    save_selection(tmp_path)
    settings = Settings(
        operator_context_path=tmp_path / "operator-context.json",
        operator_credential=SecretStr(CREDENTIAL),
    )
    if scope == "server":
        settings = settings.model_copy(update={"operator_server_url": "https://other.test"})
    else:
        settings = settings.model_copy(
            update={"operator_credential": SecretStr(f"c2o.{UUID(int=42)}.{'x' * 32}")}
        )
    original = (tmp_path / "operator-context.json").read_bytes()
    with patch("c2_relay.cli.operators.get_settings", return_value=settings):
        main(["tasks", "list"])
        client.list_tasks.assert_called_once_with(agent_id=None, status=None, limit=50, offset=0)
        with pytest.raises(SystemExit):
            main(["tasks", "show"])
        main(["context", "show"])
    output = capsys.readouterr()
    assert "No last task" in output.err
    assert (tmp_path / "operator-context.json").read_bytes() == original


def test_corrupt_context_is_sanitized_and_unrelated_commands_can_bypass_it(
    configured_cli: tuple[MagicMock, MagicMock],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, _ = configured_cli
    path = tmp_path / "operator-context.json"
    path.write_text("private-secret")
    path.chmod(0o600)
    with pytest.raises(SystemExit) as error:
        main(["tasks", "list"])
    assert error.value.code == 1
    assert "Cannot read operator context" in capsys.readouterr().err
    for command in (
        ["agents", "list"],
        ["audit", "list"],
        ["grants", "issue"],
        ["tasks", "list", "--all-agents"],
        ["tasks", "list", "--agent-id", str(AGENT_ID)],
    ):
        main(command)
    assert "private-secret" not in capsys.readouterr().out
    assert path.read_text() == "private-secret"
    assert client.list_tasks.call_count == 2


@pytest.fixture
def configured_cli(tmp_path: Path) -> Iterator[tuple[MagicMock, MagicMock]]:
    client = MagicMock()
    client.issue_grant.return_value = EnrollmentGrantIssueResponse(grant=GRANT, token=TOKEN)
    client.revoke_grant.return_value = GRANT.model_copy(
        update={"revoked_at": NOW, "revoked_by_operator_id": OPERATOR_ID}
    )
    client.list_agents.return_value = AgentListResponse(items=(), total=0, limit=50, offset=0)
    client.get_agent.return_value = RegisteredAgent(
        id=AGENT_ID,
        metadata=AgentMetadata(
            hostname="context-test",
            operating_system="Linux",
            username="user",
            agent_version="0.3.0",
        ),
        created_at=NOW,
        last_seen_at=NOW,
    )
    client.list_tasks.return_value = TaskListResponse(items=(), total=0, limit=50, offset=0)
    client.list_audit_events.return_value = AuditEventListResponse(
        items=(), total=0, limit=50, offset=0
    )
    client.get_task.return_value = Task(
        id=TASK_ID,
        agent_id=AGENT_ID,
        action=HostnameAction(),
        status=TaskStatus.COMPLETED,
        created_at=NOW,
        updated_at=NOW,
    )
    client.get_task_result.return_value = StoredResultResponse(
        item=StoredActionResult(
            result=ActionSuccess(
                task_id=TASK_ID,
                agent_id=AGENT_ID,
                completed_at=NOW,
                output=HostnameOutput(hostname="read-test"),
            ),
            received_at=NOW,
        )
    )
    with (
        patch(
            "c2_relay.cli.operators.get_settings",
            return_value=Settings(
                operator_credential=SecretStr(CREDENTIAL),
                operator_context_path=tmp_path / "operator-context.json",
            ),
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


@pytest.mark.parametrize(
    "arguments, method, expected",
    [
        (["agents", "list"], "list_agents", {"status": None, "limit": 50, "offset": 0}),
        (
            ["agents", "list", "--status", "disabled", "--limit", "1", "--offset", "2"],
            "list_agents",
            {"status": AgentStatus.DISABLED, "limit": 1, "offset": 2},
        ),
        (
            ["agents", "list", "--limit", "100", "--offset", "1000000"],
            "list_agents",
            {"status": None, "limit": 100, "offset": 1_000_000},
        ),
        (
            ["tasks", "list"],
            "list_tasks",
            {"agent_id": None, "status": None, "limit": 50, "offset": 0},
        ),
        (
            [
                "tasks",
                "list",
                "--agent-id",
                str(AGENT_ID),
                "--status",
                "completed",
                "--limit",
                "2",
                "--offset",
                "1",
            ],
            "list_tasks",
            {"agent_id": AGENT_ID, "status": TaskStatus.COMPLETED, "limit": 2, "offset": 1},
        ),
        (["tasks", "show", str(TASK_ID)], "get_task", {"task_id": TASK_ID}),
        (["tasks", "result", str(TASK_ID)], "get_task_result", {"task_id": TASK_ID}),
        (
            ["audit", "list"],
            "list_audit_events",
            {
                "event_type": None,
                "operator_id": None,
                "agent_id": None,
                "task_id": None,
                "enrollment_grant_id": None,
                "limit": 50,
                "offset": 0,
            },
        ),
        (
            [
                "audit",
                "list",
                "--event-type",
                "task.created",
                "--operator-id",
                str(OPERATOR_ID),
                "--agent-id",
                str(AGENT_ID),
                "--task-id",
                str(TASK_ID),
                "--enrollment-grant-id",
                str(GRANT_ID),
                "--limit",
                "3",
                "--offset",
                "4",
            ],
            "list_audit_events",
            {
                "event_type": AuditEventType.TASK_CREATED,
                "operator_id": OPERATOR_ID,
                "agent_id": AGENT_ID,
                "task_id": TASK_ID,
                "enrollment_grant_id": GRANT_ID,
                "limit": 3,
                "offset": 4,
            },
        ),
    ],
)
def test_operator_cli_read_commands_forward_typed_filters_and_preserve_json(
    configured_cli: tuple[MagicMock, MagicMock],
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    method: str,
    expected: dict[str, object],
) -> None:
    client, factory = configured_cli

    main([*arguments, "--server-url", "https://relay.test"])

    factory.assert_called_once_with("https://relay.test", SecretStr(CREDENTIAL), timeout=10)
    operation = getattr(client, method)
    operation.assert_called_once_with(**expected)
    output = capsys.readouterr()
    assert json.loads(output.out) == operation.return_value.model_dump(mode="json")
    assert CREDENTIAL not in output.out + output.err
    client.close.assert_called_once()
    client.issue_grant.assert_not_called()
    client.revoke_grant.assert_not_called()


@pytest.mark.parametrize("command", ["agents", "tasks", "audit"])
@pytest.mark.parametrize(
    "option, value",
    [
        ("--limit", "0"),
        ("--limit", "101"),
        ("--limit", "not-an-integer"),
        ("--offset", "-1"),
        ("--offset", "1000001"),
        ("--offset", "not-an-integer"),
    ],
)
def test_operator_cli_rejects_invalid_pagination_before_credential_input(
    configured_cli: tuple[MagicMock, MagicMock],
    command: str,
    option: str,
    value: str,
) -> None:
    _, factory = configured_cli
    with (
        patch("c2_relay.cli.operators.getpass") as prompt,
        pytest.raises(SystemExit) as error,
    ):
        main([command, "list", option, value])
    assert error.value.code == 2
    prompt.assert_not_called()
    factory.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["tasks", "show", "not-a-uuid"],
        ["tasks", "result", "not-a-uuid"],
        ["tasks", "list", "--agent-id", "not-a-uuid"],
        ["audit", "list", "--enrollment-grant-id", "not-a-uuid"],
        ["agents", "list", "--status", "unknown"],
        ["tasks", "list", "--status", "unknown"],
        ["audit", "list", "--event-type", "unknown"],
    ],
)
def test_operator_cli_rejects_invalid_identifiers_and_enum_filters(
    configured_cli: tuple[MagicMock, MagicMock],
    arguments: list[str],
) -> None:
    _, factory = configured_cli
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    factory.assert_not_called()


def test_operator_cli_invalid_read_response_is_sanitized(
    configured_cli: tuple[MagicMock, MagicMock], capsys: pytest.CaptureFixture[str]
) -> None:
    client, _ = configured_cli
    with pytest.raises(ValidationError) as validation:
        Task.model_validate({"credential": CREDENTIAL})
    client.get_task.side_effect = validation.value
    with pytest.raises(SystemExit) as error:
        main(["tasks", "show", str(TASK_ID)])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "API returned invalid response data" in output.err
    assert CREDENTIAL not in output.err + output.out
    assert output.out == ""
    client.close.assert_called_once()


@pytest.mark.parametrize("status", [401, 404])
def test_operator_cli_read_http_errors_do_not_print_raw_bodies(
    configured_cli: tuple[MagicMock, MagicMock], capsys: pytest.CaptureFixture[str], status: int
) -> None:
    client, _ = configured_cli
    request = httpx2.Request("GET", f"http://relay.test/api/v1/tasks/{TASK_ID}/result")
    client.get_task_result.side_effect = httpx2.HTTPStatusError(
        CREDENTIAL,
        request=request,
        response=httpx2.Response(status, request=request, json={"detail": TOKEN}),
    )
    with pytest.raises(SystemExit) as error:
        main(["tasks", "result", str(TASK_ID)])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert f"HTTP {status}" in output.err
    assert CREDENTIAL not in output.err + output.out
    assert TOKEN not in output.err + output.out
    assert output.out == ""
    client.close.assert_called_once()
