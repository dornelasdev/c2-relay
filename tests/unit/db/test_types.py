from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine.interfaces import Dialect

from c2_relay.db.types import UTCDateTime


@pytest.fixture
def dialect() -> Iterator[Dialect]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    yield engine.dialect
    engine.dispose()


def test_utc_datetime_handles_null_values(dialect: Dialect) -> None:
    column_type = UTCDateTime()

    assert column_type.process_bind_param(None, dialect) is None
    assert column_type.process_result_value(None, dialect) is None


def test_utc_datetime_normalizes_values_at_the_storage_boundary(dialect: Dialect) -> None:
    column_type = UTCDateTime()
    aware = datetime(2026, 1, 2, 14, tzinfo=timezone(timedelta(hours=2)))

    stored = column_type.process_bind_param(aware, dialect)
    assert stored == datetime(2026, 1, 2, 12)
    assert column_type.process_result_value(stored, dialect) == datetime(2026, 1, 2, 12, tzinfo=UTC)


def test_utc_datetime_rejects_naive_values(dialect: Dialect) -> None:
    with pytest.raises(ValueError, match="database timestamps must include a UTC offset"):
        UTCDateTime().process_bind_param(datetime(2026, 1, 2, 12), dialect)
