from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from pydantic import SecretStr

from c2_relay.cli.operators import main
from c2_relay.core.config import Settings
from c2_relay.models import Operator, OperatorId
from c2_relay.services.operators import ProvisionedOperator

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))
CREDENTIAL = "c2o.30000000-0000-4000-8000-000000000001." + "x" * 32


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
