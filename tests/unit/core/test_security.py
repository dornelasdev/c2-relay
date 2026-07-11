import pytest

from c2_relay.core.security import (
    credential_matches,
    digest_credential,
    generate_credential,
    secret_matches,
)


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


def test_secret_comparison_requires_an_exact_match() -> None:
    assert secret_matches("bootstrap", "bootstrap")
    assert not secret_matches("wrong", "bootstrap")
