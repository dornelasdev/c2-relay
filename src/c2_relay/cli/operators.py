"""Operator provisioning and API-backed enrollment commands."""

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
from c2_relay.models import EnrollmentGrantId
from c2_relay.services.operators import OperatorService


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
    for command in (issue, revoke):
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


def _manage_grant(arguments: argparse.Namespace, settings: Settings) -> None:
    request = None
    if arguments.grant_command == "issue":
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
        if request is not None:
            issued = client.issue_grant(request)
            print(issued.model_dump_json(indent=2))
        else:
            grant = client.revoke_grant(EnrollmentGrantId(arguments.grant_id))
            print(grant.model_dump_json(indent=2))
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
        _manage_grant(arguments, settings)
    except (GetPassWarning, EOFError):
        parser.exit(1, "Hidden credential input unavailable; set C2_RELAY_OPERATOR_CREDENTIAL.\n")
    except KeyboardInterrupt:
        parser.exit(1, "Operator command interrupted.\n")
    except httpx2.HTTPStatusError as error:
        parser.exit(1, f"API request failed (HTTP {error.response.status_code}).\n")
    except httpx2.HTTPError:
        parser.exit(1, "API connection failed; check the server URL, TLS, and availability.\n")
    except ValidationError:
        parser.exit(1, "Invalid grant data; check the lifetime (60-86400) and API response.\n")
    except (ValueError, httpx2.InvalidURL):
        parser.exit(1, "Invalid operator credential format or server URL.\n")
