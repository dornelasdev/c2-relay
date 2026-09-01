from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr

from c2_relay.core.security import credential_matches, digest_credential, parse_operator_credential
from c2_relay.db.schema import Base
from c2_relay.db.session import create_database_engine, create_session_factory
from c2_relay.db.uow import UnitOfWork
from c2_relay.models import Operator, OperatorId, OperatorStatus
from c2_relay.services.operators import OperatorService

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
OPERATOR_UUID = UUID("30000000-0000-4000-8000-000000000001")


def test_operator_service_provisions_identity_and_one_time_credential() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    service = OperatorService(factory, clock=lambda: NOW, id_factory=lambda: OPERATOR_UUID)

    provisioned = service.provision("Primary Operator")
    parsed = parse_operator_credential(provisioned.credential.get_secret_value())

    assert provisioned.operator.id == OperatorId(OPERATOR_UUID)
    assert provisioned.operator.name == "Primary Operator"
    assert parsed.operator_id == provisioned.operator.id

    with UnitOfWork(factory) as uow:
        stored = uow.operators.get(provisioned.operator.id)
        digest = uow.operators.credential_digest_for(provisioned.operator.id)

    assert stored == provisioned.operator
    assert digest is not None
    assert credential_matches(parsed.secret.get_secret_value(), digest)
    assert provisioned.credential.get_secret_value() not in repr(provisioned)
    engine.dispose()


def test_operator_service_rejects_a_mismatched_generated_credential() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    other_id = OperatorId(UUID("30000000-0000-4000-8000-000000000002"))
    service = OperatorService(
        factory,
        id_factory=lambda: OPERATOR_UUID,
        credential_factory=lambda operator_id: SecretStr(f"c2o.{other_id}.{'x' * 32}"),
    )

    with pytest.raises(ValueError, match="generated credential does not match operator identity"):
        service.provision("Primary Operator")

    engine.dispose()


def test_operator_service_authenticates_and_records_success() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    service = OperatorService(factory, clock=lambda: NOW, id_factory=lambda: OPERATOR_UUID)
    provisioned = service.provision("Primary Operator")

    authenticated = service.authenticate(provisioned.credential.get_secret_value())

    assert authenticated is not None
    assert authenticated.id == provisioned.operator.id
    assert authenticated.last_authenticated_at == NOW
    engine.dispose()


@pytest.mark.parametrize(
    "credential",
    [
        "malformed",
        f"c2o.{OPERATOR_UUID}.{'z' * 32}",
        "c2o.30000000-0000-4000-8000-000000000099." + "z" * 32,
    ],
)
def test_operator_service_rejects_invalid_credentials(credential: str) -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    service = OperatorService(factory, id_factory=lambda: OPERATOR_UUID)
    service.provision("Primary Operator")

    assert service.authenticate(credential) is None
    engine.dispose()


def test_operator_service_rejects_disabled_operator() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    credential = SecretStr(f"c2o.{OPERATOR_UUID}.{'x' * 32}")
    parsed = parse_operator_credential(credential.get_secret_value())
    disabled = Operator(
        id=OperatorId(OPERATOR_UUID),
        name="Disabled Operator",
        status=OperatorStatus.DISABLED,
        created_at=NOW,
    )
    with UnitOfWork(factory) as uow:
        uow.operators.add(disabled, digest_credential(parsed.secret.get_secret_value()))
        uow.commit()

    service = OperatorService(factory, clock=lambda: NOW)
    assert service.authenticate(credential.get_secret_value()) is None
    engine.dispose()
