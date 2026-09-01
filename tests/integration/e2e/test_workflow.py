from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock
from uuid import UUID

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr

from c2_relay.agent.actions import ActionRegistry
from c2_relay.agent.client import RelayClient
from c2_relay.agent.identity import IdentityStore
from c2_relay.agent.results import PendingResultStore
from c2_relay.agent.runtime import AgentRuntime
from c2_relay.api import create_app
from c2_relay.core.config import Settings
from c2_relay.db.session import create_database_engine, create_session_factory
from c2_relay.db.uow import UnitOfWork
from c2_relay.models import AgentMetadata, TaskId, TaskStatus
from c2_relay.services.operators import OperatorService

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
BOOTSTRAP_TOKEN = "b" * 32
AGENT_CREDENTIAL = "agent-credential-" + "x" * 32
OPERATOR_UUID = UUID("30000000-0000-4000-8000-000000000001")
OPERATOR_CREDENTIAL = f"c2o.{OPERATOR_UUID}.{'o' * 32}"


def test_assembled_agent_server_workflow(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'workflow.db'}"
    migration_config = Config("alembic.ini")
    migration_config.attributes["database_url"] = database_url
    command.upgrade(migration_config, "head")

    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
    OperatorService(
        factory,
        clock=lambda: NOW,
        id_factory=lambda: OPERATOR_UUID,
        credential_factory=lambda operator_id: SecretStr(OPERATOR_CREDENTIAL),
    ).provision("Workflow Operator")
    settings = Settings(database_url=database_url, bootstrap_token=SecretStr(BOOTSTRAP_TOKEN))
    app = create_app(
        settings,
        factory,
        clock=lambda: NOW,
        credential_factory=lambda: AGENT_CREDENTIAL,
    )
    identity_store = IdentityStore(tmp_path / "agent-state.json")

    with TestClient(app) as operator_client:
        relay_client = RelayClient("http://testserver", timeout=1, client=operator_client)
        runtime = AgentRuntime(
            relay_client,
            identity_store,
            PendingResultStore(tmp_path / "pending-result.json"),
            ActionRegistry(),
            bootstrap_token=SecretStr(BOOTSTRAP_TOKEN),
            poll_interval=1,
            max_backoff=2,
            metadata_factory=lambda: AgentMetadata(
                hostname="relay-host",
                operating_system="Linux",
                username="operator",
                agent_version="0.1.0",
            ),
            clock=lambda: NOW,
        )

        stopped = MagicMock(spec=Event)
        stopped.is_set.side_effect = [False, True]
        runtime.run(stopped)
        identity = identity_store.load()
        assert identity is not None

        task_response = operator_client.post(
            "/api/v1/tasks",
            headers={"Authorization": f"Bearer {OPERATOR_CREDENTIAL}"},
            json={"agent_id": str(identity.agent_id), "action": {"kind": "host.hostname"}},
        )
        assert task_response.status_code == 201
        task_id = TaskId(UUID(task_response.json()["id"]))

        runtime.run_once(identity)

    with UnitOfWork(factory) as uow:
        persisted_task = uow.tasks.get(task_id)
        persisted_result = uow.results.get(task_id)

    assert persisted_task is not None
    assert persisted_task.status is TaskStatus.COMPLETED
    assert persisted_result is not None
    assert persisted_result.status == "completed"
    assert not (tmp_path / "pending-result.json").exists()
    engine.dispose()


def test_openapi_describes_versioned_routes_and_bearer_authentication(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite+pysqlite:///{tmp_path / 'openapi.db'}")

    with TestClient(create_app(settings)) as client:
        schema = client.get("/openapi.json").json()

    assert schema["info"]["version"] == "0.1.0"
    assert "/api/v1/agents/{agent_id}/tasks/next" in schema["paths"]
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    task_operation = schema["paths"]["/api/v1/tasks"]["post"]
    assert task_operation["security"] == [{"HTTPBearer": []}]
    assert "parameters" not in task_operation
