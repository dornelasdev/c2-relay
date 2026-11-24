"""Operator provisioning, enrollment, and read-only API commands."""

import argparse
import warnings
from collections.abc import Sequence
from getpass import GetPassWarning, getpass
from uuid import UUID

import httpx2
from pydantic import SecretStr, ValidationError

from c2_relay.api.schemas import EnrollmentGrantCreateRequest
from c2_relay.cli.client import OperatorClient, normalize_server_url
from c2_relay.cli.context import OperatorContext, OperatorContextError, OperatorContextStore
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
    agent_use = agent_commands.add_parser(
        "use", help="remember an active agent for this server/operator"
    )
    agent_use.add_argument("agent_id", type=UUID)

    tasks = commands.add_parser("tasks", help="inspect tasks and stored results")
    task_commands = tasks.add_subparsers(dest="task_command", required=True)
    task_list = task_commands.add_parser("list", help="list a page of tasks")
    task_agent = task_list.add_mutually_exclusive_group()
    task_agent.add_argument("--agent-id", type=UUID)
    task_agent.add_argument("--all-agents", action="store_true", help="ignore the selected agent")
    task_list.add_argument("--status", type=TaskStatus, choices=list(TaskStatus))
    task_show = task_commands.add_parser("show", help="inspect one task")
    task_show.add_argument(
        "task_id", type=UUID, nargs="?", help="task UUID; defaults to the last task"
    )
    task_result = task_commands.add_parser("result", help="inspect a stored task result")
    task_result.add_argument(
        "task_id", type=UUID, nargs="?", help="task UUID; defaults to the last task"
    )

    audit = commands.add_parser("audit", help="inspect audit history")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    audit_list = audit_commands.add_parser("list", help="list a page of audit events")
    audit_list.add_argument("--event-type", type=AuditEventType, choices=list(AuditEventType))
    audit_list.add_argument("--operator-id", type=UUID)
    audit_list.add_argument("--agent-id", type=UUID)
    audit_list.add_argument("--task-id", type=UUID)
    audit_list.add_argument("--enrollment-grant-id", type=UUID)

    context = commands.add_parser("context", help="inspect or reset local operator selections")
    context_commands = context.add_subparsers(dest="context_command", required=True)
    context_show = context_commands.add_parser(
        "show", help="show selections for this server/operator"
    )
    context_clear = context_commands.add_parser(
        "clear", help="reset selections for this server/operator"
    )

    for command in (agent_list, task_list, audit_list):
        command.add_argument("--limit", type=_page_limit, default=50, help="page size (1-100)")
        command.add_argument("--offset", type=_page_offset, default=0, help="offset (0-1000000)")
    for command in (
        issue,
        revoke,
        agent_list,
        agent_use,
        task_list,
        task_show,
        task_result,
        audit_list,
        context_show,
        context_clear,
    ):
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


def _read_task(
    arguments: argparse.Namespace,
    client: OperatorClient,
    store: OperatorContextStore,
    server_url: str,
    operator_id: OperatorId,
) -> DomainModel:
    if arguments.task_command == "list":
        agent_id = None
        if arguments.agent_id is not None:
            agent_id = AgentId(arguments.agent_id)
        elif not arguments.all_agents:
            agent_id = store.load(server_url, operator_id).selected_agent_id
        return client.list_tasks(
            agent_id=agent_id,
            status=arguments.status,
            limit=arguments.limit,
            offset=arguments.offset,
        )
    context = store.load(server_url, operator_id)
    task_id = TaskId(arguments.task_id) if arguments.task_id is not None else context.last_task_id
    if task_id is None:
        raise OperatorContextError(
            "No last task for this server/operator. Supply a task UUID first."
        )
    response: DomainModel
    if arguments.task_command == "show":
        task = client.get_task(task_id=task_id)
        received_id = task.id
        response = task
    else:
        stored = client.get_task_result(task_id=task_id)
        received_id = stored.item.result.task_id
        response = stored
    if received_id != task_id:
        raise OperatorContextError(
            "API returned a different task. Operator context was not changed."
        )
    store.save(context.model_copy(update={"last_task_id": task_id}))
    return response


def _run_api_command(arguments: argparse.Namespace, settings: Settings) -> None:
    request = None
    if arguments.command == "grants" and arguments.grant_command == "issue":
        request = EnrollmentGrantCreateRequest(expires_in_seconds=arguments.expires_in)
    credential = settings.operator_credential
    if credential is None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            credential = SecretStr(getpass("Operator credential: "))
    parsed = parse_operator_credential(credential.get_secret_value())
    server_url = normalize_server_url(arguments.server_url or settings.operator_server_url)
    store = OperatorContextStore(settings.operator_context_path)
    if arguments.command == "context":
        context = store.load(server_url, parsed.operator_id)
        if arguments.context_command == "clear":
            context = context.model_copy(update={"selected_agent_id": None, "last_task_id": None})
            store.save(context)
        print(context.model_dump_json(indent=2))
        return
    client = OperatorClient(
        server_url,
        credential,
        timeout=settings.operator_request_timeout,
    )
    try:
        response: DomainModel | OperatorContext
        if arguments.command == "agents":
            if arguments.agent_command == "use":
                context = store.load(server_url, parsed.operator_id)
                agent_id = AgentId(arguments.agent_id)
                agent = client.get_agent(agent_id)
                if agent.id != agent_id:
                    raise OperatorContextError(
                        "API returned a different agent. Context was not changed."
                    )
                if agent.status is not AgentStatus.ACTIVE:
                    raise OperatorContextError(
                        "Selected agent is disabled. Choose an active agent."
                    )
                values: dict[str, object] = {"selected_agent_id": agent.id}
                if context.selected_agent_id != agent.id:
                    values["last_task_id"] = None
                context = context.model_copy(update=values)
                store.save(context)
                response = context
            else:
                response = client.list_agents(
                    status=arguments.status, limit=arguments.limit, offset=arguments.offset
                )
        elif arguments.command == "tasks":
            response = _read_task(arguments, client, store, server_url, parsed.operator_id)
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
    except OperatorContextError as error:
        parser.exit(1, f"{error}\n")
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
