"""Explicit transaction boundary for persistence operations."""

from types import TracebackType

from sqlalchemy.orm import Session

from c2_relay.db.repositories import (
    AgentRepository,
    OperatorRepository,
    ResultRepository,
    TaskRepository,
)
from c2_relay.db.session import SessionFactory


class UnitOfWork:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self.agents: AgentRepository
        self.operators: OperatorRepository
        self.tasks: TaskRepository
        self.results: ResultRepository

    def __enter__(self) -> "UnitOfWork":
        self.session = self._session_factory()
        self.agents = AgentRepository(self.session)
        self.operators = OperatorRepository(self.session)
        self.tasks = TaskRepository(self.session)
        self.results = ResultRepository(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if self.session is not None:
            self.session.rollback()
            self.session.close()
            self.session = None

    def commit(self) -> None:
        self._require_session().commit()

    def rollback(self) -> None:
        self._require_session().rollback()

    def _require_session(self) -> Session:
        if self.session is None:
            msg = "unit of work is not active"
            raise RuntimeError(msg)
        return self.session
