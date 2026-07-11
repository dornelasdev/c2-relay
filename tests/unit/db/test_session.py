from unittest.mock import MagicMock, patch

from sqlalchemy import Engine

from c2_relay.db.session import create_database_engine


def test_non_sqlite_engine_does_not_receive_sqlite_configuration() -> None:
    engine = MagicMock(spec=Engine)
    url = MagicMock()
    url.get_backend_name.return_value = "postgresql"

    with (
        patch("c2_relay.db.session.make_url", return_value=url),
        patch("c2_relay.db.session.create_engine", return_value=engine) as create,
    ):
        created = create_database_engine("postgresql://database", echo=True)

    assert created is engine
    create.assert_called_once_with(url, echo=True, connect_args={})
