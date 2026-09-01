"""Local operator provisioning command."""

import argparse
from collections.abc import Sequence

from c2_relay.core.config import get_settings
from c2_relay.db.session import create_database_engine, create_session_factory
from c2_relay.services.operators import OperatorService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="c2-relay-operator")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="provision a local operator identity")
    create.add_argument("--name", required=True, help="human-readable operator name")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    try:
        provisioned = OperatorService(create_session_factory(engine)).provision(arguments.name)
    finally:
        engine.dispose()

    print(f"Operator ID: {provisioned.operator.id}")
    print(f"Credential: {provisioned.credential.get_secret_value()}")
    print("Store this credential securely; it will not be shown again.")
