"""Credential storage primitives."""

from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
from re import fullmatch
from secrets import token_urlsafe
from typing import NewType
from uuid import UUID

from pydantic import SecretStr

from c2_relay.models.enrollment import EnrollmentGrantId
from c2_relay.models.operators import OperatorId

CredentialDigest = NewType("CredentialDigest", str)
IdempotencyKeyDigest = NewType("IdempotencyKeyDigest", str)
EnrollmentTokenDigest = NewType("EnrollmentTokenDigest", str)
_OPERATOR_CREDENTIAL_PREFIX = "c2o"
_ENROLLMENT_TOKEN_PREFIX = "c2g"
_OPERATOR_SECRET_PATTERN = r"[A-Za-z0-9_-]{32,128}"


@dataclass(frozen=True, slots=True)
class ParsedOperatorCredential:
    operator_id: OperatorId
    secret: SecretStr


@dataclass(frozen=True, slots=True)
class ParsedEnrollmentToken:
    grant_id: EnrollmentGrantId
    secret: SecretStr


def digest_credential(credential: str) -> CredentialDigest:
    """Create a storage-safe digest for a high-entropy bearer credential."""

    if not credential:
        msg = "credential cannot be empty"
        raise ValueError(msg)
    return CredentialDigest(sha256(credential.encode("utf-8")).hexdigest())


def digest_idempotency_key(key: str) -> IdempotencyKeyDigest:
    """Create a fixed-length storage value for an idempotency key."""

    if not key:
        msg = "idempotency key cannot be empty"
        raise ValueError(msg)
    return IdempotencyKeyDigest(sha256(key.encode("utf-8")).hexdigest())


def digest_enrollment_token(secret: str) -> EnrollmentTokenDigest:
    if not secret:
        raise ValueError("enrollment token secret cannot be empty")
    return EnrollmentTokenDigest(sha256(secret.encode("utf-8")).hexdigest())


def credential_matches(credential: str, expected: CredentialDigest) -> bool:
    return compare_digest(digest_credential(credential), expected)


def generate_credential() -> str:
    return token_urlsafe(32)


def generate_operator_credential(operator_id: OperatorId) -> SecretStr:
    secret = token_urlsafe(32)
    return SecretStr(f"{_OPERATOR_CREDENTIAL_PREFIX}.{operator_id}.{secret}")


def generate_enrollment_token(grant_id: EnrollmentGrantId) -> SecretStr:
    secret = token_urlsafe(32)
    return SecretStr(f"{_ENROLLMENT_TOKEN_PREFIX}.{grant_id}.{secret}")


def parse_operator_credential(credential: str) -> ParsedOperatorCredential:
    parts = credential.split(".")
    if len(parts) != 3 or parts[0] != _OPERATOR_CREDENTIAL_PREFIX:
        raise ValueError("invalid operator credential")

    try:
        raw_operator_id = UUID(parts[1])
    except ValueError as error:
        raise ValueError("invalid operator credential") from error

    if parts[1] != str(raw_operator_id) or fullmatch(_OPERATOR_SECRET_PATTERN, parts[2]) is None:
        raise ValueError("invalid operator credential")

    return ParsedOperatorCredential(
        operator_id=OperatorId(raw_operator_id),
        secret=SecretStr(parts[2]),
    )


def parse_enrollment_token(token: str) -> ParsedEnrollmentToken:
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _ENROLLMENT_TOKEN_PREFIX:
        raise ValueError("invalid enrollment token")
    try:
        raw_grant_id = UUID(parts[1])
    except ValueError as error:
        raise ValueError("invalid enrollment token") from error
    if parts[1] != str(raw_grant_id) or fullmatch(_OPERATOR_SECRET_PATTERN, parts[2]) is None:
        raise ValueError("invalid enrollment token")
    return ParsedEnrollmentToken(
        grant_id=EnrollmentGrantId(raw_grant_id),
        secret=SecretStr(parts[2]),
    )


def secret_matches(provided: str, expected: str) -> bool:
    return compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
