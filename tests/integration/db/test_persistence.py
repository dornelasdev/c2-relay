from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from c2_relay.core.security import digest_credential, digest_idempotency_key
from c2_relay.db.repositories import (
    AgentRepository,
    AuditRepository,
    OperatorRepository,
    ResultRepository,
    TaskRepository,
)
from c2_relay.db.schema import AgentRow, Base, OperatorRow, TaskResultRow, TaskRow
from c2_relay.db.session import create_database_engine, create_session_factory
from c2_relay.db.uow import UnitOfWork
from c2_relay.models import (
    ActionFailure,
    ActionResult,
    ActionSuccess,
    AgentId,
    AgentMetadata,
    AgentStatus,
    AuditEvent,
    AuditEventId,
    AuditEventType,
    ErrorDetail,
    HostnameAction,
    HostnameOutput,
    Operator,
    OperatorId,
    RegisteredAgent,
    Task,
    TaskId,
    TaskStatus,
    disable_agent,
    transition_task,
)

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))
TASK_ID = TaskId(UUID("10000000-0000-4000-8000-000000000001"))
OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))
AUDIT_EVENT_ID = AuditEventId(UUID("40000000-0000-4000-8000-000000000001"))


@pytest.fixture
def engine() -> Iterator[Engine]:
    value = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(value)
    yield value
    value.dispose()


def metadata() -> AgentMetadata:
    return AgentMetadata(
        hostname="relay-host",
        operating_system="Linux",
        username="operator",
        agent_version="0.1.0",
        architecture="x86_64",
    )


def task() -> Task:
    return Task(
        id=TASK_ID,
        agent_id=AGENT_ID,
        action=HostnameAction(),
        created_at=NOW,
        updated_at=NOW,
    )


def operator() -> Operator:
    return Operator(id=OPERATOR_ID, name="Primary Operator", created_at=NOW)


def add_agent(repository: AgentRepository) -> None:
    repository.add(AGENT_ID, metadata(), digest_credential("token"), now=NOW)


def add_operator(repository: OperatorRepository) -> None:
    repository.add(operator(), digest_credential("operator-secret"))


def test_agent_repository_round_trip_and_missing_lookup(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory() as session:
        repository = AgentRepository(session)
        registered = repository.add(AGENT_ID, metadata(), digest_credential("token"), now=NOW)
        session.commit()

        assert registered.metadata == metadata()
        assert repository.get(AGENT_ID) == registered
        assert repository.get(AgentId(UUID(int=0))) is None
        stored = session.get(AgentRow, AGENT_ID)
        assert stored is not None
        assert stored.credential_digest == digest_credential("token")
        assert stored.credential_digest != "token"
        assert stored.status == AgentStatus.ACTIVE.value
        assert stored.disabled_at is None
        active, total = repository.list_page(
            status=AgentStatus.ACTIVE,
            limit=10,
            offset=0,
        )
        assert active == (registered,)
        assert total == 1
        disabled, total = repository.list_page(
            status=AgentStatus.DISABLED,
            limit=10,
            offset=0,
        )
        assert disabled == ()
        assert total == 0
        page, total = repository.list_page(status=None, limit=10, offset=1)
        assert page == ()
        assert total == 1


def test_agent_repository_persists_lifecycle_updates(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory() as session:
        repository = AgentRepository(session)
        registered = repository.add(AGENT_ID, metadata(), digest_credential("token"), now=NOW)
        disabled = disable_agent(registered, at=NOW + timedelta(minutes=1))
        repository.update_lifecycle(disabled)
        session.commit()

        assert repository.get(AGENT_ID) == disabled
        stored = session.get(AgentRow, AGENT_ID)
        assert stored is not None
        assert stored.status == AgentStatus.DISABLED.value
        assert stored.disabled_at == disabled.disabled_at


def test_agent_repository_refreshes_metadata_and_rejects_missing_agent(engine: Engine) -> None:
    factory = create_session_factory(engine)
    refreshed_metadata = metadata().model_copy(
        update={"hostname": "renamed-host", "agent_version": "0.2.0", "architecture": None}
    )
    with factory() as session:
        repository = AgentRepository(session)
        original = repository.add(AGENT_ID, metadata(), digest_credential("token"), now=NOW)
        refreshed, metadata_changed = repository.refresh_metadata(
            AGENT_ID,
            refreshed_metadata,
            now=NOW + timedelta(minutes=1),
        )
        session.commit()

        assert refreshed.metadata == refreshed_metadata
        assert metadata_changed
        assert refreshed.last_seen_at == NOW + timedelta(minutes=1)
        assert refreshed.created_at == original.created_at
        assert refreshed.status is AgentStatus.ACTIVE

        with pytest.raises(KeyError):
            repository.refresh_metadata(
                AgentId(UUID(int=0)),
                refreshed_metadata,
                now=NOW,
            )


def test_agent_repository_cannot_update_a_missing_agent(engine: Engine) -> None:
    factory = create_session_factory(engine)
    missing = RegisteredAgent(
        id=AgentId(UUID(int=0)),
        metadata=metadata(),
        status=AgentStatus.DISABLED,
        created_at=NOW,
        last_seen_at=NOW,
        disabled_at=NOW,
    )
    with factory() as session, pytest.raises(KeyError):
        AgentRepository(session).update_lifecycle(missing)


def test_operator_repository_round_trip_and_authentication_update(engine: Engine) -> None:
    factory = create_session_factory(engine)
    digest = digest_credential("operator-secret")
    with factory() as session:
        repository = OperatorRepository(session)
        repository.add(operator(), digest)
        session.commit()

        assert repository.get(OPERATOR_ID) == operator()
        assert repository.get(OperatorId(UUID(int=0))) is None
        assert repository.credential_digest_for(OPERATOR_ID) == digest
        assert repository.credential_digest_for(OperatorId(UUID(int=0))) is None

        authenticated_at = NOW + timedelta(seconds=1)
        authenticated = repository.mark_authenticated(OPERATOR_ID, now=authenticated_at)
        session.commit()
        assert authenticated.last_authenticated_at == authenticated_at
        assert repository.get(OPERATOR_ID) == authenticated

        stored = session.get(OperatorRow, OPERATOR_ID)
        assert stored is not None
        assert stored.credential_digest == digest
        assert stored.credential_digest != "operator-secret"


def test_audit_repository_is_append_only_filterable_history(engine: Engine) -> None:
    factory = create_session_factory(engine)
    enrolled = AuditEvent(
        id=AUDIT_EVENT_ID,
        event_type=AuditEventType.AGENT_ENROLLED,
        occurred_at=NOW,
        agent_id=AGENT_ID,
    )
    created = AuditEvent(
        id=AuditEventId(UUID("40000000-0000-4000-8000-000000000002")),
        event_type=AuditEventType.TASK_CREATED,
        occurred_at=NOW + timedelta(seconds=1),
        operator_id=OPERATOR_ID,
        agent_id=AGENT_ID,
        task_id=TASK_ID,
    )
    with factory() as session:
        repository = AuditRepository(session)
        repository.add(enrolled)
        repository.add(created)
        session.commit()

        page, total = repository.list_page(
            event_type=None,
            operator_id=None,
            agent_id=None,
            task_id=None,
            limit=1,
            offset=0,
        )
        assert page == (created,)
        assert total == 2

        page, total = repository.list_page(
            event_type=AuditEventType.TASK_CREATED,
            operator_id=OPERATOR_ID,
            agent_id=AGENT_ID,
            task_id=TASK_ID,
            limit=10,
            offset=0,
        )
        assert page == (created,)
        assert total == 1


def test_operator_repository_rejects_missing_authentication_update(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory() as session, pytest.raises(KeyError):
        OperatorRepository(session).mark_authenticated(OperatorId(UUID(int=0)), now=NOW)


def test_sqlite_foreign_keys_are_enabled(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_task_repository_round_trip_update_and_missing_paths(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory() as session:
        agents = AgentRepository(session)
        tasks = TaskRepository(session)
        add_agent(agents)
        tasks.add(task())
        session.commit()

        assert tasks.get(TASK_ID) == task()
        assert tasks.get(TaskId(UUID(int=0))) is None
        stored = session.get(TaskRow, TASK_ID)
        assert stored is not None
        assert stored.created_by_operator_id is None
        assert stored.idempotency_key_digest is None

        running = transition_task(
            transition_task(
                task(),
                TaskStatus.CLAIMED,
                at=NOW + timedelta(seconds=1),
                lease_expires_at=NOW + timedelta(seconds=31),
            ),
            TaskStatus.RUNNING,
            at=NOW + timedelta(seconds=2),
        )
        tasks.update(running)
        session.commit()
        assert tasks.get(TASK_ID) == running
        page, total = tasks.list_page(
            agent_id=AGENT_ID,
            status=TaskStatus.RUNNING,
            limit=10,
            offset=0,
        )
        assert page == (running,)
        assert total == 1
        page, total = tasks.list_page(
            agent_id=None,
            status=None,
            limit=10,
            offset=1,
        )
        assert page == ()
        assert total == 1

        with pytest.raises(KeyError):
            tasks.update(running.model_copy(update={"id": TaskId(UUID(int=0))}))

        with pytest.raises(ValueError, match="finished task must be completed or failed"):
            tasks.finish_claimed(task(), lease_valid_at=NOW)


def test_task_repository_scopes_idempotency_keys_to_operators(engine: Engine) -> None:
    factory = create_session_factory(engine)
    key_digest = digest_idempotency_key("task-request-0001")
    second_operator_id = OperatorId(UUID("30000000-0000-4000-8000-000000000002"))
    second_task = task().model_copy(
        update={"id": TaskId(UUID("10000000-0000-4000-8000-000000000002"))}
    )
    with factory() as session:
        add_agent(AgentRepository(session))
        operators = OperatorRepository(session)
        add_operator(operators)
        operators.add(
            Operator(id=second_operator_id, name="Second Operator", created_at=NOW),
            digest_credential("second-operator-secret"),
        )
        tasks = TaskRepository(session)
        first, first_created = tasks.add_idempotent(
            task(),
            operator_id=OPERATOR_ID,
            key_digest=key_digest,
        )
        repeated, repeated_created = tasks.add_idempotent(
            second_task,
            operator_id=OPERATOR_ID,
            key_digest=key_digest,
        )
        scoped, scoped_created = tasks.add_idempotent(
            second_task,
            operator_id=second_operator_id,
            key_digest=key_digest,
        )
        session.commit()

        assert (first, first_created) == (task(), True)
        assert (repeated, repeated_created) == (task(), False)
        assert (scoped, scoped_created) == (second_task, True)
        assert tasks.get_by_idempotency_key(OPERATOR_ID, key_digest) == task()
        assert (
            tasks.get_by_idempotency_key(
                OPERATOR_ID,
                digest_idempotency_key("missing-task-key"),
            )
            is None
        )
        stored = session.get(TaskRow, TASK_ID)
        assert stored is not None
        assert stored.idempotency_key_digest == key_digest
        assert stored.idempotency_key_digest != "task-request-0001"


def test_task_idempotency_re_raises_unrelated_integrity_errors(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory() as session, pytest.raises(IntegrityError):
        TaskRepository(session).add_idempotent(
            task(),
            operator_id=OPERATOR_ID,
            key_digest=digest_idempotency_key("task-request-0001"),
        )


def test_sqlite_task_creation_is_idempotent_under_concurrency(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'idempotency.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    key_digest = digest_idempotency_key("concurrent-task-request")
    task_ids = (
        TASK_ID,
        TaskId(UUID("10000000-0000-4000-8000-000000000002")),
    )
    with factory() as session:
        add_agent(AgentRepository(session))
        add_operator(OperatorRepository(session))
        session.commit()

    barrier = Barrier(2)

    def insert_task(task_id: TaskId) -> tuple[TaskId, bool]:
        candidate = task().model_copy(update={"id": task_id})
        with factory() as session:
            barrier.wait()
            stored, created = TaskRepository(session).add_idempotent(
                candidate,
                operator_id=OPERATOR_ID,
                key_digest=key_digest,
            )
            session.commit()
            return stored.id, created

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(insert_task, task_ids))

    assert sorted(created for _, created in outcomes) == [False, True]
    assert len({task_id for task_id, _ in outcomes}) == 1
    with factory() as session:
        _, total = TaskRepository(session).list_page(
            agent_id=None,
            status=None,
            limit=10,
            offset=0,
        )
    assert total == 1
    engine.dispose()


def test_task_repository_claims_and_recovers_expired_work(engine: Engine) -> None:
    factory = create_session_factory(engine)
    lease_duration = timedelta(seconds=30)
    with factory() as session:
        agents = AgentRepository(session)
        tasks = TaskRepository(session)
        add_agent(agents)
        tasks.add(task())
        session.commit()

        claimed = tasks.claim_next(AGENT_ID, now=NOW, lease_duration=lease_duration)
        session.commit()
        assert claimed is not None
        assert claimed.status is TaskStatus.CLAIMED
        assert claimed.lease_expires_at == NOW + lease_duration
        assert tasks.claim_next(AGENT_ID, now=NOW, lease_duration=lease_duration) is None

        recovered = tasks.claim_next(
            AGENT_ID,
            now=NOW + lease_duration,
            lease_duration=lease_duration,
        )
        session.commit()
        assert recovered is not None
        assert recovered.id == claimed.id
        assert recovered.lease_expires_at == NOW + lease_duration * 2

        with pytest.raises(ValueError, match="lease_duration must be positive"):
            tasks.claim_next(AGENT_ID, now=NOW, lease_duration=timedelta(0))


def test_sqlite_claim_is_atomic_for_concurrent_pollers(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'claims.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    second_id = TaskId(UUID("10000000-0000-4000-8000-000000000002"))
    with factory() as session:
        agents = AgentRepository(session)
        tasks = TaskRepository(session)
        add_agent(agents)
        tasks.add(task())
        tasks.add(task().model_copy(update={"id": second_id}))
        session.commit()

    barrier = Barrier(2)

    def claim() -> TaskId | None:
        with factory() as session:
            barrier.wait()
            claimed = TaskRepository(session).claim_next(
                AGENT_ID,
                now=NOW,
                lease_duration=timedelta(seconds=30),
            )
            session.commit()
            return None if claimed is None else claimed.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed_ids = set(executor.map(lambda _: claim(), range(2)))

    assert claimed_ids == {TASK_ID, second_id}
    engine.dispose()


def test_sqlite_cancellation_and_completion_have_one_winner(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'cancellation.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    claimed = transition_task(
        task(),
        TaskStatus.CLAIMED,
        at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=31),
    )
    running = transition_task(claimed, TaskStatus.RUNNING, at=NOW + timedelta(seconds=2))
    completed = transition_task(running, TaskStatus.COMPLETED, at=NOW + timedelta(seconds=2))
    with factory() as session:
        add_agent(AgentRepository(session))
        tasks = TaskRepository(session)
        tasks.add(task())
        tasks.update(claimed)
        session.commit()

    barrier = Barrier(2)

    def cancel() -> TaskStatus:
        with factory() as session:
            barrier.wait()
            current, _ = TaskRepository(session).cancel_if_active(
                TASK_ID,
                now=NOW + timedelta(seconds=2),
            )
            session.commit()
            assert current is not None
            return current.status

    def finish() -> bool:
        with factory() as session:
            barrier.wait()
            updated = TaskRepository(session).finish_claimed(
                completed,
                lease_valid_at=NOW + timedelta(seconds=2),
            )
            session.commit()
            return updated

    with ThreadPoolExecutor(max_workers=2) as executor:
        cancel_future = executor.submit(cancel)
        finish_future = executor.submit(finish)
        cancellation_status = cancel_future.result()
        completion_won = finish_future.result()

    with factory() as session:
        final = TaskRepository(session).get(TASK_ID)

    assert final is not None
    assert (final.status, completion_won) in {
        (TaskStatus.CANCELLED, False),
        (TaskStatus.COMPLETED, True),
    }
    assert cancellation_status is final.status
    engine.dispose()


def test_sqlite_result_insert_is_idempotent_under_concurrency(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'results.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    result = ActionSuccess(
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        completed_at=NOW,
        output=HostnameOutput(hostname="relay-host"),
    )
    with factory() as session:
        add_agent(AgentRepository(session))
        TaskRepository(session).add(task())
        session.commit()

    barrier = Barrier(2)

    def insert_result() -> bool:
        with factory() as session:
            barrier.wait()
            inserted = ResultRepository(session).add(result, received_at=NOW)
            session.commit()
            return inserted

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _: insert_result(), range(2)))

    assert outcomes == [False, True]
    engine.dispose()


@pytest.mark.parametrize("failed", [False, True])
def test_result_repository_round_trip(engine: Engine, failed: bool) -> None:
    factory = create_session_factory(engine)
    with factory() as session:
        agents = AgentRepository(session)
        tasks = TaskRepository(session)
        results = ResultRepository(session)
        add_agent(agents)
        tasks.add(task())
        result: ActionResult
        if failed:
            result = ActionFailure(
                task_id=TASK_ID,
                agent_id=AGENT_ID,
                completed_at=NOW,
                error=ErrorDetail(code="action_failed", message="failure"),
            )
        else:
            result = ActionSuccess(
                task_id=TASK_ID,
                agent_id=AGENT_ID,
                completed_at=NOW,
                output=HostnameOutput(hostname="relay-host"),
            )
        assert results.add(result, received_at=NOW + timedelta(seconds=1))
        session.commit()

        assert results.get(TASK_ID) == result
        assert results.get(TaskId(UUID(int=0))) is None
        stored = session.get(TaskResultRow, TASK_ID)
        assert stored is not None
        assert stored.received_at == NOW + timedelta(seconds=1)
        stored_result = results.get_stored(TASK_ID)
        assert stored_result is not None
        assert stored_result.result == result
        assert stored_result.received_at == NOW + timedelta(seconds=1)
        assert results.get_stored(TaskId(UUID(int=0))) is None
        session.expunge(stored)
        assert not results.add(result, received_at=NOW + timedelta(seconds=2))


def test_foreign_key_violation_is_rejected(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory() as session, pytest.raises(IntegrityError):
        TaskRepository(session).add(task())


def test_unit_of_work_commits_and_rolls_back(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with UnitOfWork(factory) as uow:
        uow.agents.add(AGENT_ID, metadata(), digest_credential("token"), now=NOW)
        uow.operators.add(operator(), digest_credential("operator-secret"))
        uow.commit()

    with UnitOfWork(factory) as uow:
        assert uow.agents.get(AGENT_ID) is not None
        assert uow.operators.get(OPERATOR_ID) == operator()
        uow.tasks.add(task())
        uow.rollback()

    with UnitOfWork(factory) as uow:
        assert uow.tasks.get(TASK_ID) is None


def test_unit_of_work_requires_an_active_context(engine: Engine) -> None:
    uow = UnitOfWork(create_session_factory(engine))

    with pytest.raises(RuntimeError, match="unit of work is not active"):
        uow.commit()

    uow.__exit__(None, None, None)
