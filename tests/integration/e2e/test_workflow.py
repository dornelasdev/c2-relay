from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import UUID

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr

from c2_relay.agent.actions import ActionRegistry
from c2_relay.agent.client import RelayClient
from c2_relay.agent.identity import IdentityStore
from c2_relay.agent.runtime import AgentRuntime
from c2_relay.api import create_app
from c2_relay.core.config import Settings
from c2_relay.db.session import create_database_engine, create_session_factory
from c2_relay.db.uow import UnitOfWork
from c2_relay.models import AgentMetadata, TaskId, TaskStatus

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
BOOTSTRAP_TOKEN = "b" * 32
AGENT_CREDENTIAL = "agent-credential-" + "x" * 32


def test_assembled_agent_server_workflow(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'workflow.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")

    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
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

        stopped = Event()
        stopped.set()
        runtime.run(stopped)
        identity = identity_store.load()
        assert identity is not None

        task_response = operator_client.post(
            "/api/v1/tasks",
            headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
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
