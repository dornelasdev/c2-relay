"""Reusable HTTP authentication dependencies."""

from http import HTTPStatus
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from c2_relay.models import Operator
from c2_relay.services.operators import OperatorService

bearer = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=HTTPStatus.UNAUTHORIZED,
        detail="invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


class RequireOperator:
    def __init__(self, service: OperatorService) -> None:
        self._service = service

    def __call__(
        self,
        authorization: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
    ) -> Operator:
        if authorization is None or authorization.scheme.lower() != "bearer":
            raise unauthorized()

        operator = self._service.authenticate(authorization.credentials)
        if operator is None:
            raise unauthorized()
        return operator
