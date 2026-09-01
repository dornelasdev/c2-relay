"""Operator identity application workflows."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import SecretStr

from c2_relay.core.security import (
    credential_matches,
    digest_credential,
    generate_operator_credential,
    parse_operator_credential,
)
from c2_relay.db.session import SessionFactory
from c2_relay.db.uow import UnitOfWork
from c2_relay.models import Operator, OperatorId, OperatorStatus

_DUMMY_SECRET = "x" * 32
_DUMMY_DIGEST = digest_credential(_DUMMY_SECRET)


@dataclass(frozen=True, slots=True)
class ProvisionedOperator:
    operator: Operator
    credential: SecretStr


class OperatorService:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], UUID] = uuid4,
        credential_factory: Callable[[OperatorId], SecretStr] = generate_operator_credential,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._id_factory = id_factory
        self._credential_factory = credential_factory

    def provision(self, name: str) -> ProvisionedOperator:
        operator_id = OperatorId(self._id_factory())
        credential = self._credential_factory(operator_id)
        parsed = parse_operator_credential(credential.get_secret_value())
        if parsed.operator_id != operator_id:
            msg = "generated credential does not match operator identity"
            raise ValueError(msg)

        operator = Operator(id=operator_id, name=name, created_at=self._clock())
        secret_digest = digest_credential(parsed.secret.get_secret_value())
        with UnitOfWork(self._session_factory) as uow:
            uow.operators.add(operator, secret_digest)
            uow.commit()

        return ProvisionedOperator(operator=operator, credential=credential)

    def authenticate(self, credential: str) -> Operator | None:
        try:
            parsed = parse_operator_credential(credential)
        except ValueError:
            credential_matches(_DUMMY_SECRET, _DUMMY_DIGEST)
            return None

        with UnitOfWork(self._session_factory) as uow:
            operator = uow.operators.get(parsed.operator_id)
            expected = uow.operators.credential_digest_for(parsed.operator_id)
            secret_matches = credential_matches(
                parsed.secret.get_secret_value(),
                expected or _DUMMY_DIGEST,
            )
            if (
                operator is None
                or operator.status is not OperatorStatus.ACTIVE
                or not secret_matches
            ):
                return None

            authenticated = uow.operators.mark_authenticated(operator.id, now=self._clock())
            uow.commit()
            return authenticated
