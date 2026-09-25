"""Mappings between domain contracts and relational rows."""

from datetime import datetime, timedelta

from pydantic import TypeAdapter
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from c2_relay.core.security import CredentialDigest, IdempotencyKeyDigest
from c2_relay.db.schema import AgentRow, AuditEventRow, OperatorRow, TaskResultRow, TaskRow
from c2_relay.models import (
    ActionResult,
    AgentId,
    AgentMetadata,
    AgentStatus,
    AuditEvent,
    AuditEventId,
    AuditEventType,
    Operator,
    OperatorId,
    OperatorStatus,
    RegisteredAgent,
    StoredActionResult,
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
            status=AgentStatus.ACTIVE.value,
            created_at=now,
            last_seen_at=now,
            disabled_at=None,
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

    def lock_for_update(self, agent_id: AgentId, *, active_only: bool = False) -> bool:
        filters = [AgentRow.id == agent_id]
        if active_only:
            filters.append(AgentRow.status == AgentStatus.ACTIVE.value)
        # SQLite ignores SELECT FOR UPDATE; this no-op write serializes polls and disables.
        locked_id = self._session.scalar(
            update(AgentRow).where(*filters).values(status=AgentRow.status).returning(AgentRow.id)
        )
        return locked_id is not None

    def list_page(
        self,
        *,
        status: AgentStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[RegisteredAgent, ...], int]:
        filters = () if status is None else (AgentRow.status == status.value,)
        total = self._session.scalar(select(func.count()).select_from(AgentRow).where(*filters))
        rows = self._session.scalars(
            select(AgentRow)
            .where(*filters)
            .order_by(AgentRow.created_at, AgentRow.id)
            .limit(limit)
            .offset(offset)
        )
        return tuple(self._to_domain(row) for row in rows), int(total or 0)

    def refresh_metadata(
        self,
        agent_id: AgentId,
        metadata: AgentMetadata,
        *,
        now: datetime,
    ) -> tuple[RegisteredAgent, bool]:
        row = self._session.get(AgentRow, agent_id)
        if row is None:
            raise KeyError(agent_id)
        metadata_changed = self._to_domain(row).metadata != metadata
        row.hostname = metadata.hostname
        row.operating_system = metadata.operating_system
        row.username = metadata.username
        row.agent_version = metadata.agent_version
        row.architecture = metadata.architecture
        row.last_seen_at = now
        self._session.flush()
        return self._to_domain(row), metadata_changed

    def update_lifecycle(self, agent: RegisteredAgent) -> None:
        row = self._session.get(AgentRow, agent.id)
        if row is None:
            raise KeyError(agent.id)
        row.status = agent.status.value
        row.disabled_at = agent.disabled_at
        self._session.flush()

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
            status=AgentStatus(row.status),
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            disabled_at=row.disabled_at,
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


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: AuditEvent) -> None:
        self._session.add(
            AuditEventRow(
                id=event.id,
                event_type=event.event_type.value,
                occurred_at=event.occurred_at,
                operator_id=event.operator_id,
                agent_id=event.agent_id,
                task_id=event.task_id,
            )
        )
        self._session.flush()

    def list_page(
        self,
        *,
        event_type: AuditEventType | None,
        operator_id: OperatorId | None,
        agent_id: AgentId | None,
        task_id: TaskId | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[AuditEvent, ...], int]:
        filters = []
        if event_type is not None:
            filters.append(AuditEventRow.event_type == event_type.value)
        if operator_id is not None:
            filters.append(AuditEventRow.operator_id == operator_id)
        if agent_id is not None:
            filters.append(AuditEventRow.agent_id == agent_id)
        if task_id is not None:
            filters.append(AuditEventRow.task_id == task_id)
        total = self._session.scalar(
            select(func.count()).select_from(AuditEventRow).where(*filters)
        )
        rows = self._session.scalars(
            select(AuditEventRow)
            .where(*filters)
            .order_by(AuditEventRow.occurred_at.desc(), AuditEventRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return tuple(self._to_domain(row) for row in rows), int(total or 0)

    @staticmethod
    def _to_domain(row: AuditEventRow) -> AuditEvent:
        return AuditEvent(
            id=AuditEventId(row.id),
            event_type=AuditEventType(row.event_type),
            occurred_at=row.occurred_at,
            operator_id=None if row.operator_id is None else OperatorId(row.operator_id),
            agent_id=AgentId(row.agent_id),
            task_id=None if row.task_id is None else TaskId(row.task_id),
        )


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        task: Task,
        *,
        created_by_operator_id: OperatorId | None = None,
        idempotency_key_digest: IdempotencyKeyDigest | None = None,
    ) -> None:
        self._session.add(
            TaskRow(
                id=task.id,
                agent_id=task.agent_id,
                created_by_operator_id=created_by_operator_id,
                idempotency_key_digest=idempotency_key_digest,
                action=task.action.model_dump(mode="json"),
                status=task.status.value,
                created_at=task.created_at,
                updated_at=task.updated_at,
                lease_expires_at=task.lease_expires_at,
            )
        )
        self._session.flush()

    def get_by_idempotency_key(
        self,
        operator_id: OperatorId,
        key_digest: IdempotencyKeyDigest,
    ) -> Task | None:
        row = self._session.scalar(
            select(TaskRow).where(
                TaskRow.created_by_operator_id == operator_id,
                TaskRow.idempotency_key_digest == key_digest,
            )
        )
        return None if row is None else self._to_domain(row)

    def add_idempotent(
        self,
        task: Task,
        *,
        operator_id: OperatorId,
        key_digest: IdempotencyKeyDigest,
    ) -> tuple[Task, bool]:
        try:
            with self._session.begin_nested():
                self.add(
                    task,
                    created_by_operator_id=operator_id,
                    idempotency_key_digest=key_digest,
                )
        except IntegrityError:
            existing = self.get_by_idempotency_key(operator_id, key_digest)
            if existing is None:
                raise
            return existing, False
        return task, True

    def get(self, task_id: TaskId) -> Task | None:
        row = self._session.get(TaskRow, task_id)
        if row is None:
            return None
        return self._to_domain(row)

    @staticmethod
    def _to_domain(row: TaskRow) -> Task:
        return Task(
            id=TaskId(row.id),
            agent_id=AgentId(row.agent_id),
            action=_ACTION_ADAPTER.validate_python(row.action),
            status=TaskStatus(row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
            lease_expires_at=row.lease_expires_at,
        )

    def list_page(
        self,
        *,
        agent_id: AgentId | None,
        status: TaskStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[Task, ...], int]:
        filters = []
        if agent_id is not None:
            filters.append(TaskRow.agent_id == agent_id)
        if status is not None:
            filters.append(TaskRow.status == status.value)
        total = self._session.scalar(select(func.count()).select_from(TaskRow).where(*filters))
        rows = self._session.scalars(
            select(TaskRow)
            .where(*filters)
            .order_by(TaskRow.created_at, TaskRow.id)
            .limit(limit)
            .offset(offset)
        )
        return tuple(self._to_domain(row) for row in rows), int(total or 0)

    def update(self, task: Task) -> None:
        row = self._session.get(TaskRow, task.id)
        if row is None:
            raise KeyError(task.id)
        row.status = task.status.value
        row.updated_at = task.updated_at
        row.lease_expires_at = task.lease_expires_at
        self._session.flush()

    def cancel_if_active(self, task_id: TaskId, *, now: datetime) -> tuple[Task | None, bool]:
        cancelled_id = self._session.scalar(
            update(TaskRow)
            .where(
                TaskRow.id == task_id,
                TaskRow.status.in_(
                    (
                        TaskStatus.QUEUED.value,
                        TaskStatus.CLAIMED.value,
                        TaskStatus.RUNNING.value,
                    )
                ),
            )
            .values(
                status=TaskStatus.CANCELLED.value,
                updated_at=now,
                lease_expires_at=None,
            )
            .returning(TaskRow.id)
        )
        self._session.flush()
        return self.get(task_id), cancelled_id is not None

    def finish_claimed(self, task: Task, *, lease_valid_at: datetime) -> bool:
        if task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            raise ValueError("finished task must be completed or failed")
        updated_id = self._session.scalar(
            update(TaskRow)
            .where(
                TaskRow.id == task.id,
                TaskRow.status == TaskStatus.CLAIMED.value,
                TaskRow.lease_expires_at > lease_valid_at,
            )
            .values(
                status=task.status.value,
                updated_at=task.updated_at,
                lease_expires_at=None,
            )
            .returning(TaskRow.id)
        )
        self._session.flush()
        return updated_id is not None

    def claim_next(
        self,
        agent_id: AgentId,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> Task | None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")

        self._session.execute(
            update(TaskRow)
            .where(
                TaskRow.agent_id == agent_id,
                TaskRow.status == TaskStatus.CLAIMED.value,
                TaskRow.lease_expires_at <= now,
            )
            .values(
                status=TaskStatus.QUEUED.value,
                updated_at=now,
                lease_expires_at=None,
            )
        )
        candidate = (
            select(TaskRow.id)
            .where(TaskRow.agent_id == agent_id, TaskRow.status == TaskStatus.QUEUED.value)
            .order_by(TaskRow.created_at, TaskRow.id)
            .limit(1)
            .scalar_subquery()
        )
        claimed_id = self._session.scalar(
            update(TaskRow)
            .where(
                TaskRow.id == candidate,
                TaskRow.status == TaskStatus.QUEUED.value,
            )
            .values(
                status=TaskStatus.CLAIMED.value,
                updated_at=now,
                lease_expires_at=now + lease_duration,
            )
            .returning(TaskRow.id)
        )
        self._session.flush()
        return None if claimed_id is None else self.get(TaskId(claimed_id))


class ResultRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, result: ActionResult, *, received_at: datetime) -> bool:
        serialized = result.model_dump(mode="json")
        payload_key = "output" if result.status == "completed" else "error"
        try:
            with self._session.begin_nested():
                self._session.add(
                    TaskResultRow(
                        task_id=result.task_id,
                        agent_id=result.agent_id,
                        status=result.status,
                        completed_at=result.completed_at,
                        received_at=received_at,
                        payload=serialized[payload_key],
                    )
                )
                self._session.flush()
        except IntegrityError:
            return False
        return True

    def get(self, task_id: TaskId) -> ActionResult | None:
        row = self._session.get(TaskResultRow, task_id)
        if row is None:
            return None
        return self._to_domain(row)

    def get_stored(self, task_id: TaskId) -> StoredActionResult | None:
        row = self._session.get(TaskResultRow, task_id)
        if row is None:
            return None
        return StoredActionResult(result=self._to_domain(row), received_at=row.received_at)

    @staticmethod
    def _to_domain(row: TaskResultRow) -> ActionResult:
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
