"""Mappings between domain contracts and relational rows."""

from datetime import datetime

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from c2_relay.core.security import CredentialDigest
from c2_relay.db.schema import AgentRow, OperatorRow, TaskResultRow, TaskRow
from c2_relay.models import (
    ActionResult,
    AgentId,
    AgentMetadata,
    Operator,
    OperatorId,
    OperatorStatus,
    RegisteredAgent,
    Task,
    TaskId,
    TaskStatus,
)
from c2_relay.models.actions import ActionRequest

_ACTION_ADAPTER: TypeAdapter[ActionRequest] = TypeAdapter(ActionRequest)
_RESULT_ADAPTER: TypeAdapter[ActionResult] = TypeAdapter(ActionResult)


class AgentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        agent_id: AgentId,
        metadata: AgentMetadata,
        credential_digest: CredentialDigest,
        *,
        now: datetime,
    ) -> RegisteredAgent:
        row = AgentRow(
            id=agent_id,
            credential_digest=credential_digest,
            created_at=now,
            last_seen_at=now,
            **metadata.model_dump(),
        )
        self._session.add(row)
        self._session.flush()
        return self._to_domain(row)

    def get(self, agent_id: AgentId) -> RegisteredAgent | None:
        row = self._session.get(AgentRow, agent_id)
        return None if row is None else self._to_domain(row)

    def credential_digest_for(self, agent_id: AgentId) -> CredentialDigest | None:
        row = self._session.get(AgentRow, agent_id)
        return None if row is None else CredentialDigest(row.credential_digest)

    def touch(self, agent_id: AgentId, *, now: datetime) -> RegisteredAgent:
        row = self._session.get(AgentRow, agent_id)
        if row is None:
            raise KeyError(agent_id)
        row.last_seen_at = now
        self._session.flush()
        return self._to_domain(row)

    @staticmethod
    def _to_domain(row: AgentRow) -> RegisteredAgent:
        return RegisteredAgent(
            id=AgentId(row.id),
            metadata=AgentMetadata(
                hostname=row.hostname,
                operating_system=row.operating_system,
                username=row.username,
                agent_version=row.agent_version,
                architecture=row.architecture,
            ),
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
        )


class OperatorRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, operator: Operator, credential_digest: CredentialDigest) -> None:
        self._session.add(
            OperatorRow(
                id=operator.id,
                name=operator.name,
                credential_digest=credential_digest,
                status=operator.status.value,
                created_at=operator.created_at,
                last_authenticated_at=operator.last_authenticated_at,
            )
        )
        self._session.flush()

    def get(self, operator_id: OperatorId) -> Operator | None:
        row = self._session.get(OperatorRow, operator_id)
        return None if row is None else self._to_domain(row)

    def credential_digest_for(self, operator_id: OperatorId) -> CredentialDigest | None:
        row = self._session.get(OperatorRow, operator_id)
        return None if row is None else CredentialDigest(row.credential_digest)

    def mark_authenticated(self, operator_id: OperatorId, *, now: datetime) -> Operator:
        row = self._session.get(OperatorRow, operator_id)
        if row is None:
            raise KeyError(operator_id)
        row.last_authenticated_at = now
        self._session.flush()
        return self._to_domain(row)

    @staticmethod
    def _to_domain(row: OperatorRow) -> Operator:
        return Operator(
            id=OperatorId(row.id),
            name=row.name,
            status=OperatorStatus(row.status),
            created_at=row.created_at,
            last_authenticated_at=row.last_authenticated_at,
        )


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, task: Task) -> None:
        self._session.add(
            TaskRow(
                id=task.id,
                agent_id=task.agent_id,
                action=task.action.model_dump(mode="json"),
                status=task.status.value,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )
        self._session.flush()

    def get(self, task_id: TaskId) -> Task | None:
        row = self._session.get(TaskRow, task_id)
        if row is None:
            return None
        return Task(
            id=TaskId(row.id),
            agent_id=AgentId(row.agent_id),
            action=_ACTION_ADAPTER.validate_python(row.action),
            status=TaskStatus(row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def update(self, task: Task) -> None:
        row = self._session.get(TaskRow, task.id)
        if row is None:
            raise KeyError(task.id)
        row.status = task.status.value
        row.updated_at = task.updated_at
        self._session.flush()

    def next_queued(self, agent_id: AgentId) -> Task | None:
        row = self._session.scalar(
            select(TaskRow)
            .where(TaskRow.agent_id == agent_id, TaskRow.status == "queued")
            .order_by(TaskRow.created_at, TaskRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return None if row is None else self.get(TaskId(row.id))


class ResultRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, result: ActionResult) -> None:
        serialized = result.model_dump(mode="json")
        payload_key = "output" if result.status == "completed" else "error"
        self._session.add(
            TaskResultRow(
                task_id=result.task_id,
                agent_id=result.agent_id,
                status=result.status,
                completed_at=result.completed_at,
                payload=serialized[payload_key],
            )
        )
        self._session.flush()

    def get(self, task_id: TaskId) -> ActionResult | None:
        row = self._session.get(TaskResultRow, task_id)
        if row is None:
            return None
        payload_key = "output" if row.status == "completed" else "error"
        return _RESULT_ADAPTER.validate_python(
            {
                "status": row.status,
                "task_id": row.task_id,
                "agent_id": row.agent_id,
                "completed_at": row.completed_at,
                payload_key: row.payload,
            }
        )
