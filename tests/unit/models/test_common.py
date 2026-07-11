from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from c2_relay.models import UtcDateTime


def test_utc_datetime_normalizes_an_aware_timestamp() -> None:
    adapter = TypeAdapter(UtcDateTime)
    value = datetime(2026, 1, 2, 12, tzinfo=timezone(timedelta(hours=2)))

    assert adapter.validate_python(value) == datetime(2026, 1, 2, 10, tzinfo=UTC)


def test_utc_datetime_rejects_a_naive_timestamp() -> None:
    adapter = TypeAdapter(UtcDateTime)

    with pytest.raises(ValidationError, match="timestamp must include a UTC offset"):
        adapter.validate_python(datetime(2026, 1, 2, 12))
