from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from c2_relay.core.security import digest_credential
from c2_relay.db.repositories import (
    AgentRepository,
    OperatorRepository,
    ResultRepository,
    TaskRepository,
)
from c2_relay.db.schema import AgentRow, Base, OperatorRow
from c2_relay.db.session import create_database_engine, create_session_factory
from c2_relay.db.uow import UnitOfWork
from c2_relay.models import (
    ActionFailure,
    ActionResult,
    ActionSuccess,
    AgentId,
    AgentMetadata,
    AgentStatus,
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


def test_agent_repository_cannot_touch_a_missing_agent(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory() as session, pytest.raises(KeyError):
        AgentRepository(session).touch(AgentId(UUID(int=0)), now=NOW)


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

        with pytest.raises(KeyError):
            tasks.update(running.model_copy(update={"id": TaskId(UUID(int=0))}))


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
        results.add(result)
        session.commit()

        assert results.get(TASK_ID) == result
        assert results.get(TaskId(UUID(int=0))) is None


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
