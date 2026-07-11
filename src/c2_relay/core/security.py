"""Credential storage primitives."""

from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from typing import NewType

CredentialDigest = NewType("CredentialDigest", str)


def digest_credential(credential: str) -> CredentialDigest:
    """Create a storage-safe digest for a high-entropy bearer credential."""

    if not credential:
        msg = "credential cannot be empty"
        raise ValueError(msg)
    return CredentialDigest(sha256(credential.encode("utf-8")).hexdigest())


def credential_matches(credential: str, expected: CredentialDigest) -> bool:
    return compare_digest(digest_credential(credential), expected)


def generate_credential() -> str:
    return token_urlsafe(32)


def secret_matches(provided: str, expected: str) -> bool:
    return compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
