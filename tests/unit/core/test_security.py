from uuid import UUID

import pytest
from pydantic import SecretStr

from c2_relay.core.security import (
    credential_matches,
    digest_credential,
    digest_enrollment_token,
    digest_idempotency_key,
    generate_credential,
    generate_enrollment_token,
    generate_operator_credential,
    parse_enrollment_token,
    parse_operator_credential,
    secret_matches,
)
from c2_relay.models import EnrollmentGrantId, OperatorId

OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))
GRANT_ID = EnrollmentGrantId(UUID("50000000-0000-4000-8000-000000000001"))


def test_credential_digest_is_deterministic_and_storage_safe() -> None:
    digest = digest_credential("high-entropy-token")

    assert len(digest) == 64
    assert "high-entropy-token" not in digest
    assert credential_matches("high-entropy-token", digest)
    assert not credential_matches("wrong-token", digest)


def test_empty_credentials_are_rejected() -> None:
    with pytest.raises(ValueError, match="credential cannot be empty"):
        digest_credential("")


def test_idempotency_key_digest_is_deterministic_and_storage_safe() -> None:
    digest = digest_idempotency_key("task-request-0001")

    assert len(digest) == 64
    assert "task-request-0001" not in digest
    assert digest == digest_idempotency_key("task-request-0001")
    assert digest != digest_idempotency_key("task-request-0002")

    with pytest.raises(ValueError, match="idempotency key cannot be empty"):
        digest_idempotency_key("")


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


def test_enrollment_tokens_are_scoped_parseable_and_storage_safe() -> None:
    first = generate_enrollment_token(GRANT_ID)
    second = generate_enrollment_token(GRANT_ID)

    assert first != second
    parsed = parse_enrollment_token(first.get_secret_value())
    assert parsed.grant_id == GRANT_ID
    digest = digest_enrollment_token(parsed.secret.get_secret_value())
    assert len(digest) == 64
    assert parsed.secret.get_secret_value() not in digest


@pytest.mark.parametrize(
    "token",
    [
        "",
        "c2g",
        "wrong.50000000-0000-4000-8000-000000000001." + "x" * 32,
        "c2g.not-a-uuid." + "x" * 32,
        "c2g.50000000000040008000000000000001." + "x" * 32,
        "c2g.50000000-0000-4000-8000-000000000001.short",
        "c2g.50000000-0000-4000-8000-000000000001." + "x" * 31 + "!",
        "c2g.50000000-0000-4000-8000-000000000001." + "x" * 32 + ".extra",
    ],
)
def test_enrollment_tokens_reject_malformed_values(token: str) -> None:
    with pytest.raises(ValueError, match="invalid enrollment token"):
        parse_enrollment_token(token)


def test_empty_enrollment_token_secret_is_rejected() -> None:
    with pytest.raises(ValueError, match="secret cannot be empty"):
        digest_enrollment_token("")


def test_secret_comparison_requires_an_exact_match() -> None:
    assert secret_matches("bootstrap", "bootstrap")
    assert not secret_matches("wrong", "bootstrap")
