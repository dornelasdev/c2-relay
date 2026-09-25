from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from c2_relay.db.repositories import TaskRepository
from c2_relay.models.common import AgentId, TaskId
from c2_relay.models.tasks import TaskStatus


def test_migrations_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config = Config("alembic.ini")
    config.attributes["database_url"] = f"sqlite+pysqlite:///{database_path}"

    command.upgrade(config, "20260711_0001")

    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    assert set(inspect(engine).get_table_names()) == {
        "agents",
        "alembic_version",
        "tasks",
        "task_results",
    }
    assert "status" not in {column["name"] for column in inspect(engine).get_columns("agents")}

    command.upgrade(config, "head")
    assert set(inspect(engine).get_table_names()) == {
        "agents",
        "alembic_version",
        "audit_events",
        "operators",
        "tasks",
        "task_results",
    }
    agent_columns = {column["name"] for column in inspect(engine).get_columns("agents")}
    assert {"status", "disabled_at"} <= agent_columns
    task_columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
    assert {
        "lease_expires_at",
        "created_by_operator_id",
        "idempotency_key_digest",
    } <= task_columns
    task_unique_constraints = {
        constraint["name"] for constraint in inspect(engine).get_unique_constraints("tasks")
    }
    assert "uq_tasks_operator_idempotency_key" in task_unique_constraints
    task_foreign_keys = {
        constraint["name"] for constraint in inspect(engine).get_foreign_keys("tasks")
    }
    assert "fk_tasks_created_by_operator_id_operators" in task_foreign_keys
    result_columns = {column["name"] for column in inspect(engine).get_columns("task_results")}
    assert "received_at" in result_columns
    constraints = {
        constraint["name"] for constraint in inspect(engine).get_check_constraints("agents")
    }
    assert constraints == {"ck_agents_status"}
    audit_indexes = {index["name"] for index in inspect(engine).get_indexes("audit_events")}
    assert audit_indexes == {
        "ix_audit_events_agent_occurred",
        "ix_audit_events_occurred",
        "ix_audit_events_operator_occurred",
        "ix_audit_events_task_occurred",
        "ix_audit_events_type_occurred",
    }

    command.downgrade(config, "20260902_0007")
    assert "lease_expires_at" in {column["name"] for column in inspect(engine).get_columns("tasks")}

    command.downgrade(config, "20260902_0006")
    task_columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
    assert "created_by_operator_id" not in task_columns
    assert "idempotency_key_digest" not in task_columns

    command.downgrade(config, "20260901_0005")
    assert "audit_events" not in inspect(engine).get_table_names()

    command.downgrade(config, "20260901_0004")
    result_columns = {column["name"] for column in inspect(engine).get_columns("task_results")}
    assert "received_at" not in result_columns

    command.downgrade(config, "20260901_0003")
    task_columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
    assert "lease_expires_at" not in task_columns

    command.downgrade(config, "20260901_0002")
    assert "operators" in inspect(engine).get_table_names()
    agent_columns = {column["name"] for column in inspect(engine).get_columns("agents")}
    assert "status" not in agent_columns
    assert "disabled_at" not in agent_columns

    command.downgrade(config, "20260711_0001")
    assert "operators" not in inspect(engine).get_table_names()

    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()


def test_upgrade_requeues_claims_from_populated_v01_database(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    config = Config("alembic.ini")
    config.attributes["database_url"] = f"sqlite+pysqlite:///{database_path}"
    command.upgrade(config, "20260711_0001")

    engine = create_engine(f"sqlite+pysqlite:///{database_path}", poolclass=NullPool)
    metadata = MetaData()
    agents = Table("agents", metadata, autoload_with=engine)
    tasks = Table("tasks", metadata, autoload_with=engine)
    agent_id = uuid4()
    claimed_id, queued_id, completed_id = uuid4(), uuid4(), uuid4()
    old_time = datetime(2026, 1, 1, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            agents.insert().values(
                id=agent_id.hex,
                credential_digest="a" * 64,
                hostname="legacy-host",
                operating_system="Linux",
                username="tester",
                agent_version="0.1.0",
                created_at=old_time,
                last_seen_at=old_time,
            )
        )
        connection.execute(
            tasks.insert(),
            [
                {
                    "id": task_id.hex,
                    "agent_id": agent_id.hex,
                    "action": {"kind": "host.hostname"},
                    "status": status,
                    "created_at": old_time + timedelta(seconds=offset),
                    "updated_at": old_time + timedelta(seconds=offset),
                }
                for task_id, status, offset in (
                    (claimed_id, "claimed", 0),
                    (queued_id, "queued", 1),
                    (completed_id, "completed", 2),
                )
            ],
        )

    command.upgrade(config, "20260902_0007")
    tasks = Table("tasks", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        legacy_claim = connection.execute(
            select(tasks.c.status, tasks.c.lease_expires_at).where(tasks.c.id == claimed_id.hex)
        ).one()
    assert legacy_claim == ("claimed", None)

    command.upgrade(config, "head")
    with Session(engine) as session:
        repository = TaskRepository(session)
        recovered = repository.get(TaskId(claimed_id))
        assert recovered is not None
        assert recovered.status is TaskStatus.QUEUED
        assert recovered.lease_expires_at is None
        queued = repository.get(TaskId(queued_id))
        completed = repository.get(TaskId(completed_id))
        assert queued is not None and queued.status is TaskStatus.QUEUED
        assert completed is not None and completed.status is TaskStatus.COMPLETED
        claimed = repository.claim_next(
            AgentId(agent_id),
            now=datetime.now(UTC),
            lease_duration=timedelta(minutes=5),
        )
        assert claimed is not None
        assert claimed.id == TaskId(claimed_id)
        assert claimed.status is TaskStatus.CLAIMED
        assert claimed.lease_expires_at is not None

    engine.dispose()


def test_migrations_use_runtime_database_setting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "configured.db"
    monkeypatch.setenv(
        "C2_RELAY_DATABASE_URL",
        f"sqlite+pysqlite:///{database_path}",
    )

    command.upgrade(Config("alembic.ini"), "head")

    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    assert "operators" in inspect(engine).get_table_names()
    engine.dispose()
