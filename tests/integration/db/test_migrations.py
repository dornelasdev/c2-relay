from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_migrations_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path}")

    command.upgrade(config, "20260711_0001")

    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    assert set(inspect(engine).get_table_names()) == {
        "agents",
        "alembic_version",
        "tasks",
        "task_results",
    }

    command.upgrade(config, "head")
    assert set(inspect(engine).get_table_names()) == {
        "agents",
        "alembic_version",
        "operators",
        "tasks",
        "task_results",
    }

    command.downgrade(config, "20260711_0001")
    assert "operators" not in inspect(engine).get_table_names()

    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()
