from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


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
        "operators",
        "tasks",
        "task_results",
    }
    agent_columns = {column["name"] for column in inspect(engine).get_columns("agents")}
    assert {"status", "disabled_at"} <= agent_columns
    task_columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
    assert "lease_expires_at" in task_columns
    constraints = {
        constraint["name"] for constraint in inspect(engine).get_check_constraints("agents")
    }
    assert constraints == {"ck_agents_status"}

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
