from uuid import UUID

import pytest
from pydantic import SecretStr

from c2_relay.core.security import (
    credential_matches,
    digest_credential,
    generate_credential,
    generate_operator_credential,
    parse_operator_credential,
    secret_matches,
)
from c2_relay.models import OperatorId

OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))


def test_credential_digest_is_deterministic_and_storage_safe() -> None:
    digest = digest_credential("high-entropy-token")

    assert len(digest) == 64
    assert "high-entropy-token" not in digest
    assert credential_matches("high-entropy-token", digest)
    assert not credential_matches("wrong-token", digest)


def test_empty_credentials_are_rejected() -> None:
    with pytest.raises(ValueError, match="credential cannot be empty"):
        digest_credential("")


def test_generated_credentials_have_high_entropy() -> None:
    first = generate_credential()
    second = generate_credential()

    assert len(first) >= 32
    assert first != second


def test_operator_credentials_are_scoped_and_parseable() -> None:
    first = generate_operator_credential(OPERATOR_ID)
    second = generate_operator_credential(OPERATOR_ID)

    assert isinstance(first, SecretStr)
    assert first != second
    assert str(first) == "**********"

    parsed = parse_operator_credential(first.get_secret_value())
    assert parsed.operator_id == OPERATOR_ID
    assert len(parsed.secret.get_secret_value()) >= 32
    assert str(parsed.secret) == "**********"


@pytest.mark.parametrize(
    "credential",
    [
        "",
        "c2o",
        "wrong.30000000-0000-4000-8000-000000000001." + "x" * 32,
        "c2o.not-a-uuid." + "x" * 32,
        "c2o.30000000000040008000000000000001." + "x" * 32,
        "c2o.30000000-0000-4000-8000-000000000001.short",
        "c2o.30000000-0000-4000-8000-000000000001." + "x" * 31 + "!",
        "c2o.30000000-0000-4000-8000-000000000001." + "x" * 32 + ".extra",
    ],
)
def test_operator_credentials_reject_malformed_values(credential: str) -> None:
    with pytest.raises(ValueError, match="invalid operator credential"):
        parse_operator_credential(credential)


def test_secret_comparison_requires_an_exact_match() -> None:
    assert secret_matches("bootstrap", "bootstrap")
    assert not secret_matches("wrong", "bootstrap")
