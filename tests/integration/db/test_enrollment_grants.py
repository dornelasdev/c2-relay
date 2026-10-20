from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from c2_relay.core.security import digest_credential, digest_enrollment_token
from c2_relay.db.repositories import (
    AgentRepository,
    EnrollmentGrantRepository,
    OperatorRepository,
)
from c2_relay.db.schema import Base, EnrollmentGrantRow
from c2_relay.db.session import create_database_engine
from c2_relay.models import (
    AgentId,
    AgentMetadata,
    EnrollmentGrant,
    EnrollmentGrantId,
    Operator,
    OperatorId,
)

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
GRANT_ID = EnrollmentGrantId(UUID("50000000-0000-4000-8000-000000000001"))
OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))
TOKEN = "test-enrollment-token-" + "x" * 32


@pytest.fixture(params=["metadata", "migration"])
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite+pysqlite:///{tmp_path / 'grants.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    value = create_database_engine(url)
    try:
        if request.param == "migration":
            command.upgrade(config, "20260925_0008")
        else:
            Base.metadata.create_all(value)
        with Session(value) as session:
            OperatorRepository(session).add(
                Operator(id=OPERATOR_ID, name="Issuer", created_at=NOW),
                digest_credential("operator-secret"),
            )
            AgentRepository(session).add(
                AGENT_ID,
                AgentMetadata(
                    hostname="relay-host",
                    operating_system="Linux",
                    username="operator",
                    agent_version="0.2.1",
                ),
                digest_credential("agent-secret"),
                now=NOW,
            )
            session.commit()
        if request.param == "migration":
            command.upgrade(config, "head")
        yield value
    finally:
        value.dispose()


def row_values() -> dict[str, object]:
    return {
        "id": GRANT_ID,
        "issued_by_operator_id": OPERATOR_ID,
        "token_digest": digest_enrollment_token(TOKEN),
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
    }


def test_grant_storage_preserves_existing_identities_and_keeps_only_digest(engine: Engine) -> None:
    with Session(engine) as session:
        assert OperatorRepository(session).get(OPERATOR_ID) is not None
        assert AgentRepository(session).get(AGENT_ID) is not None
        session.add(EnrollmentGrantRow(**row_values()))
        session.commit()

    with Session(engine) as session:
        stored = session.get(EnrollmentGrantRow, GRANT_ID)
        assert stored is not None
        assert stored.token_digest == digest_enrollment_token(TOKEN)
        assert stored.token_digest != TOKEN
        grant = EnrollmentGrant.model_validate(stored, from_attributes=True)
        assert grant.id == GRANT_ID
        assert grant.created_at == NOW
        assert grant.expires_at == NOW + timedelta(minutes=15)
        assert "token_digest" not in grant.model_dump()

    columns = {column["name"] for column in inspect(engine).get_columns("enrollment_grants")}
    assert columns == set(EnrollmentGrant.model_fields) | {"token_digest"}


@pytest.mark.parametrize(
    "outcome",
    [
        {"redeemed_at": NOW, "redeemed_by_agent_id": AGENT_ID},
        {"revoked_at": NOW + timedelta(hours=1), "revoked_by_operator_id": OPERATOR_ID},
    ],
)
def test_grant_storage_accepts_valid_outcomes(engine: Engine, outcome: dict[str, object]) -> None:
    with Session(engine) as session:
        session.add(EnrollmentGrantRow(**(row_values() | outcome)))
        session.commit()
        stored = session.get(EnrollmentGrantRow, GRANT_ID)
        assert stored is not None
        grant = EnrollmentGrant.model_validate(stored, from_attributes=True)
        for field, value in outcome.items():
            assert getattr(grant, field) == value


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": NOW},
        {"redeemed_at": NOW},
        {"redeemed_by_agent_id": AGENT_ID},
        {"revoked_at": NOW},
        {"revoked_by_operator_id": OPERATOR_ID},
        {
            "redeemed_at": NOW,
            "redeemed_by_agent_id": AGENT_ID,
            "revoked_at": NOW,
            "revoked_by_operator_id": OPERATOR_ID,
        },
        {"redeemed_at": NOW - timedelta(seconds=1), "redeemed_by_agent_id": AGENT_ID},
        {"redeemed_at": NOW + timedelta(minutes=15), "redeemed_by_agent_id": AGENT_ID},
        {"revoked_at": NOW - timedelta(seconds=1), "revoked_by_operator_id": OPERATOR_ID},
        {"token_digest": "short"},
        {"issued_by_operator_id": UUID(int=0)},
        {"redeemed_at": NOW, "redeemed_by_agent_id": UUID(int=0)},
        {"revoked_at": NOW, "revoked_by_operator_id": UUID(int=0)},
    ],
)
def test_database_rejects_invalid_grants(engine: Engine, changes: dict[str, object]) -> None:
    with Session(engine) as session, pytest.raises(IntegrityError):
        session.add(EnrollmentGrantRow(**(row_values() | changes)))
        session.commit()


def test_grant_digest_is_unique(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(EnrollmentGrantRow(**row_values()))
        session.commit()
        session.add(EnrollmentGrantRow(**(row_values() | {"id": UUID(int=2)})))
        with pytest.raises(IntegrityError):
            session.commit()


def test_grant_repository_adds_gets_and_revokes_once(engine: Engine) -> None:
    grant = EnrollmentGrant(
        id=GRANT_ID,
        issued_by_operator_id=OPERATOR_ID,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    with Session(engine) as session:
        repository = EnrollmentGrantRepository(session)
        repository.add(grant, digest_enrollment_token(TOKEN))
        session.commit()
        assert repository.get(GRANT_ID) == grant
        assert repository.get(EnrollmentGrantId(UUID(int=0))) is None

        revoked, transitioned = repository.revoke_if_pending(
            GRANT_ID,
            operator_id=OPERATOR_ID,
            now=NOW + timedelta(minutes=16),
        )
        session.commit()
        assert transitioned is True
        assert revoked is not None
        assert revoked.revoked_at == NOW + timedelta(minutes=16)
        assert revoked.revoked_by_operator_id == OPERATOR_ID

        repeated, transitioned = repository.revoke_if_pending(
            GRANT_ID,
            operator_id=OPERATOR_ID,
            now=NOW + timedelta(minutes=17),
        )
        assert transitioned is False
        assert repeated == revoked


def test_grant_repository_does_not_revoke_redeemed_or_missing_grant(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            EnrollmentGrantRow(
                **(row_values() | {"redeemed_at": NOW, "redeemed_by_agent_id": AGENT_ID})
            )
        )
        session.commit()
        repository = EnrollmentGrantRepository(session)

        redeemed, transitioned = repository.revoke_if_pending(
            GRANT_ID,
            operator_id=OPERATOR_ID,
            now=NOW + timedelta(seconds=1),
        )
        assert transitioned is False
        assert redeemed is not None and redeemed.redeemed_at == NOW

        missing, transitioned = repository.revoke_if_pending(
            EnrollmentGrantId(UUID(int=0)),
            operator_id=OPERATOR_ID,
            now=NOW,
        )
        assert transitioned is False
        assert missing is None


def test_grant_repository_persists_redemption_and_refuses_reuse(engine: Engine) -> None:
    with Session(engine) as session:
        repository = EnrollmentGrantRepository(session)
        repository.add(
            EnrollmentGrant(
                id=GRANT_ID,
                issued_by_operator_id=OPERATOR_ID,
                created_at=NOW,
                expires_at=NOW + timedelta(minutes=15),
            ),
            digest_enrollment_token(TOKEN),
        )
        session.commit()
        assert repository.redeem_if_pending(GRANT_ID, agent_id=AGENT_ID, now=NOW)
        session.commit()

    with Session(engine) as session:
        repository = EnrollmentGrantRepository(session)
        grant = repository.get(GRANT_ID)
        assert grant is not None
        assert grant.redeemed_at == NOW
        assert grant.redeemed_by_agent_id == AGENT_ID
        assert not repository.redeem_if_pending(
            GRANT_ID, agent_id=AGENT_ID, now=NOW + timedelta(seconds=1)
        )
