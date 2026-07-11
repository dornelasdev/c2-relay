from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from c2_relay.api import create_app
from c2_relay.core.config import Settings


def test_app_disposes_an_internally_owned_engine(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite+pysqlite:///{tmp_path / 'owned.db'}")

    with (
        patch.object(Engine, "dispose", autospec=True) as dispose,
        TestClient(create_app(settings)) as client,
    ):
        assert client.get("/api/v1/health").status_code == 200

    dispose.assert_called_once()
