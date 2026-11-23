"""Operator provisioning, enrollment, and read-only API commands."""

import argparse
import warnings
from collections.abc import Sequence
from getpass import GetPassWarning, getpass
from uuid import UUID

import httpx2
from pydantic import SecretStr, ValidationError

from c2_relay.api.schemas import EnrollmentGrantCreateRequest
from c2_relay.cli.client import OperatorClient
from c2_relay.core.config import Settings, get_settings
from c2_relay.core.security import parse_operator_credential
from c2_relay.db.session import create_database_engine, create_session_factory
from c2_relay.models import (
    AgentId,
    AgentStatus,
    AuditEventType,
    DomainModel,
    EnrollmentGrantId,
    OperatorId,
    TaskId,
    TaskStatus,
)
from c2_relay.services.operators import OperatorService


def _page_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("limit must be between 1 and 100")
    return parsed


def _page_offset(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed <= 1_000_000:
        raise argparse.ArgumentTypeError("offset must be between 0 and 1000000")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="c2-relay-operator")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="provision a local operator identity")
    create.add_argument("--name", required=True, help="human-readable operator name")
    grants = commands.add_parser("grants", help="manage enrollment grants through the API")
    grant_commands = grants.add_subparsers(dest="grant_command", required=True)
    issue = grant_commands.add_parser("issue", help="issue a single-use enrollment token")
    issue.add_argument("--expires-in", type=int, default=900, help="lifetime in seconds (60-86400)")
    revoke = grant_commands.add_parser("revoke", help="revoke an unused enrollment grant")
    revoke.add_argument("grant_id", type=UUID, help="enrollment grant UUID")

    agents = commands.add_parser("agents", help="inspect agent inventory")
    agent_commands = agents.add_subparsers(dest="agent_command", required=True)
    agent_list = agent_commands.add_parser("list", help="list a page of agents")
    agent_list.add_argument("--status", type=AgentStatus, choices=list(AgentStatus))

    tasks = commands.add_parser("tasks", help="inspect tasks and stored results")
    task_commands = tasks.add_subparsers(dest="task_command", required=True)
    task_list = task_commands.add_parser("list", help="list a page of tasks")
    task_list.add_argument("--agent-id", type=UUID)
    task_list.add_argument("--status", type=TaskStatus, choices=list(TaskStatus))
    task_show = task_commands.add_parser("show", help="inspect one task")
    task_show.add_argument("task_id", type=UUID)
    task_result = task_commands.add_parser("result", help="inspect a stored task result")
    task_result.add_argument("task_id", type=UUID)

    audit = commands.add_parser("audit", help="inspect audit history")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    audit_list = audit_commands.add_parser("list", help="list a page of audit events")
    audit_list.add_argument("--event-type", type=AuditEventType, choices=list(AuditEventType))
    audit_list.add_argument("--operator-id", type=UUID)
    audit_list.add_argument("--agent-id", type=UUID)
    audit_list.add_argument("--task-id", type=UUID)
    audit_list.add_argument("--enrollment-grant-id", type=UUID)

    for command in (agent_list, task_list, audit_list):
        command.add_argument("--limit", type=_page_limit, default=50, help="page size (1-100)")
        command.add_argument("--offset", type=_page_offset, default=0, help="offset (0-1000000)")
    for command in (issue, revoke, agent_list, task_list, task_show, task_result, audit_list):
        command.add_argument("--server-url", help="API origin; defaults to operator settings")
    return parser


def _provision(name: str, settings: Settings) -> None:
    engine = create_database_engine(settings.database_url)
    try:
        provisioned = OperatorService(create_session_factory(engine)).provision(name)
    finally:
        engine.dispose()

    print(f"Operator ID: {provisioned.operator.id}")
    print(f"Credential: {provisioned.credential.get_secret_value()}")
    print("Store this credential securely; it will not be shown again.")


def _read_task(arguments: argparse.Namespace, client: OperatorClient) -> DomainModel:
    if arguments.task_command == "list":
        return client.list_tasks(
            agent_id=AgentId(arguments.agent_id) if arguments.agent_id is not None else None,
            status=arguments.status,
            limit=arguments.limit,
            offset=arguments.offset,
        )
    if arguments.task_command == "show":
        return client.get_task(task_id=TaskId(arguments.task_id))
    return client.get_task_result(task_id=TaskId(arguments.task_id))


def _run_api_command(arguments: argparse.Namespace, settings: Settings) -> None:
    request = None
    if arguments.command == "grants" and arguments.grant_command == "issue":
        request = EnrollmentGrantCreateRequest(expires_in_seconds=arguments.expires_in)
    credential = settings.operator_credential
    if credential is None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            credential = SecretStr(getpass("Operator credential: "))
    parse_operator_credential(credential.get_secret_value())
    client = OperatorClient(
        arguments.server_url or settings.operator_server_url,
        credential,
        timeout=settings.operator_request_timeout,
    )
    try:
        response: DomainModel
        if arguments.command == "agents":
            response = client.list_agents(
                status=arguments.status, limit=arguments.limit, offset=arguments.offset
            )
        elif arguments.command == "tasks":
            response = _read_task(arguments, client)
        elif arguments.command == "audit":
            response = client.list_audit_events(
                event_type=arguments.event_type,
                operator_id=(
                    OperatorId(arguments.operator_id) if arguments.operator_id is not None else None
                ),
                agent_id=AgentId(arguments.agent_id) if arguments.agent_id is not None else None,
                task_id=TaskId(arguments.task_id) if arguments.task_id is not None else None,
                enrollment_grant_id=(
                    EnrollmentGrantId(arguments.enrollment_grant_id)
                    if arguments.enrollment_grant_id is not None
                    else None
                ),
                limit=arguments.limit,
                offset=arguments.offset,
            )
        elif request is not None:
            response = client.issue_grant(request)
        else:
            response = client.revoke_grant(EnrollmentGrantId(arguments.grant_id))
        print(response.model_dump_json(indent=2))
    finally:
        client.close()


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        settings = get_settings()
    except ValidationError:
        parser.exit(2, "Invalid configuration; check the C2_RELAY_ settings.\n")
    if arguments.command == "create":
        _provision(arguments.name, settings)
        return
    try:
        _run_api_command(arguments, settings)
    except (GetPassWarning, EOFError):
        parser.exit(1, "Hidden credential input unavailable; set C2_RELAY_OPERATOR_CREDENTIAL.\n")
    except KeyboardInterrupt:
        parser.exit(1, "Operator command interrupted.\n")
    except httpx2.HTTPStatusError as error:
        parser.exit(1, f"API request failed (HTTP {error.response.status_code}).\n")
    except httpx2.HTTPError:
        parser.exit(1, "API connection failed; check the server URL, TLS, and availability.\n")
    except ValidationError:
        message = (
            "Invalid grant data; check the lifetime (60-86400) and API response."
            if arguments.command == "grants"
            else "API returned invalid response data."
        )
        parser.exit(1, f"{message}\n")
    except (ValueError, httpx2.InvalidURL):
        parser.exit(1, "Invalid operator credential format or server URL.\n")
