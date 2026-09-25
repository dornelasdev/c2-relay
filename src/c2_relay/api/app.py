"""FastAPI application factory and versioned routes."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials

from c2_relay import __version__
from c2_relay.api.authentication import RequireOperator, bearer, unauthorized
from c2_relay.api.middleware import (
    AuthenticationFailureLimitMiddleware,
    RequestSizeLimitMiddleware,
)
from c2_relay.api.schemas import (
    ActionResultSubmission,
    ActionSuccessSubmission,
    AgentListResponse,
    AuditEventListResponse,
    CheckInRequest,
    CheckInResponse,
    EnrollmentRequest,
    EnrollmentResponse,
    HealthResponse,
    StoredResultResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
)
from c2_relay.core.config import Settings, get_settings
from c2_relay.core.security import (
    credential_matches,
    digest_credential,
    digest_idempotency_key,
    generate_credential,
    secret_matches,
)
from c2_relay.db.session import SessionFactory, create_database_engine, create_session_factory
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
    Operator,
    OperatorId,
    RegisteredAgent,
    Task,
    TaskId,
    TaskStatus,
    disable_agent,
    transition_task,
)
from c2_relay.services.operators import OperatorService

MAX_REQUEST_BYTES = 64 * 1024
_DUMMY_AGENT_DIGEST = digest_credential("x" * 32)


def _audit_event(
    event_type: AuditEventType,
    *,
    occurred_at: datetime,
    agent_id: AgentId,
    operator_id: OperatorId | None = None,
    task_id: TaskId | None = None,
) -> AuditEvent:
    return AuditEvent(
        id=AuditEventId(uuid4()),
        event_type=event_type,
        occurred_at=occurred_at,
        operator_id=operator_id,
        agent_id=agent_id,
        task_id=task_id,
    )


def _require_bootstrap(settings: Settings, provided: str | None) -> None:
    configured = settings.bootstrap_token
    if configured is None:
        raise HTTPException(HTTPStatus.SERVICE_UNAVAILABLE, "bootstrap operations are disabled")
    if provided is None or not secret_matches(provided, configured.get_secret_value()):
        raise unauthorized()


def _require_agent(
    factory: SessionFactory,
    agent_id: AgentId,
    authorization: HTTPAuthorizationCredentials | None,
) -> None:
    if authorization is None or authorization.scheme.lower() != "bearer":
        raise unauthorized()
    with UnitOfWork(factory) as uow:
        agent = uow.agents.get(agent_id)
        expected = uow.agents.credential_digest_for(agent_id)
        matches = credential_matches(
            authorization.credentials,
            expected or _DUMMY_AGENT_DIGEST,
        )
        if agent is None or agent.status is not AgentStatus.ACTIVE or not matches:
            raise unauthorized()


def _router(
    settings: Settings,
    factory: SessionFactory,
    clock: Callable[[], datetime],
    credential_factory: Callable[[], str],
    operator_service: OperatorService,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    require_operator = RequireOperator(operator_service)

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @router.get("/agents", response_model=AgentListResponse)
    def list_agents(
        operator: Annotated[Operator, Depends(require_operator)],
        status: AgentStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> AgentListResponse:
        del operator
        with UnitOfWork(factory) as uow:
            agents, total = uow.agents.list_page(
                status=status,
                limit=limit,
                offset=offset,
            )
        return AgentListResponse(items=agents, total=total, limit=limit, offset=offset)

    @router.get("/audit-events", response_model=AuditEventListResponse)
    def list_audit_events(
        operator: Annotated[Operator, Depends(require_operator)],
        event_type: AuditEventType | None = None,
        operator_id: OperatorId | None = None,
        agent_id: AgentId | None = None,
        task_id: TaskId | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> AuditEventListResponse:
        del operator
        with UnitOfWork(factory) as uow:
            events, total = uow.audit_events.list_page(
                event_type=event_type,
                operator_id=operator_id,
                agent_id=agent_id,
                task_id=task_id,
                limit=limit,
                offset=offset,
            )
        return AuditEventListResponse(items=events, total=total, limit=limit, offset=offset)

    @router.get("/tasks", response_model=TaskListResponse)
    def list_tasks(
        operator: Annotated[Operator, Depends(require_operator)],
        agent_id: AgentId | None = None,
        status: TaskStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> TaskListResponse:
        del operator
        with UnitOfWork(factory) as uow:
            tasks, total = uow.tasks.list_page(
                agent_id=agent_id,
                status=status,
                limit=limit,
                offset=offset,
            )
        return TaskListResponse(items=tasks, total=total, limit=limit, offset=offset)

    @router.get("/tasks/{task_id}", response_model=Task)
    def get_task(
        task_id: TaskId,
        operator: Annotated[Operator, Depends(require_operator)],
    ) -> Task:
        del operator
        with UnitOfWork(factory) as uow:
            task = uow.tasks.get(task_id)
        if task is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, "task not found")
        return task

    @router.get("/tasks/{task_id}/result", response_model=StoredResultResponse)
    def get_task_result(
        task_id: TaskId,
        operator: Annotated[Operator, Depends(require_operator)],
    ) -> StoredResultResponse:
        del operator
        with UnitOfWork(factory) as uow:
            result = uow.results.get_stored(task_id)
        if result is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, "result not found")
        return StoredResultResponse(item=result)

    @router.post("/tasks/{task_id}/cancel", response_model=Task)
    def cancel_task(
        task_id: TaskId,
        operator: Annotated[Operator, Depends(require_operator)],
    ) -> Task:
        now = clock()
        with UnitOfWork(factory) as uow:
            task, transitioned = uow.tasks.cancel_if_active(task_id, now=now)
            if task is None:
                raise HTTPException(HTTPStatus.NOT_FOUND, "task not found")
            if task.status is not TaskStatus.CANCELLED:
                raise HTTPException(
                    HTTPStatus.CONFLICT,
                    f"task cannot be cancelled from {task.status.value}",
                )
            if transitioned:
                uow.audit_events.add(
                    _audit_event(
                        AuditEventType.TASK_CANCELLED,
                        occurred_at=now,
                        operator_id=operator.id,
                        agent_id=task.agent_id,
                        task_id=task.id,
                    )
                )
            uow.commit()
        return task

    @router.post("/agents/enroll", response_model=EnrollmentResponse, status_code=201)
    def enroll(
        request: EnrollmentRequest,
        bootstrap_token: str | None = Header(default=None, alias="X-Bootstrap-Token"),
    ) -> EnrollmentResponse:
        _require_bootstrap(settings, bootstrap_token)
        agent_id = AgentId(uuid4())
        credential = credential_factory()
        now = clock()
        with UnitOfWork(factory) as uow:
            uow.agents.add(
                agent_id,
                request,
                digest_credential(credential),
                now=now,
            )
            uow.audit_events.add(
                _audit_event(
                    AuditEventType.AGENT_ENROLLED,
                    occurred_at=now,
                    agent_id=agent_id,
                )
            )
            uow.commit()
        return EnrollmentResponse(agent_id=agent_id, credential=credential)

    @router.post("/agents/{agent_id}/check-ins", response_model=CheckInResponse)
    def check_in(
        agent_id: AgentId,
        request: CheckInRequest,
        authorization: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
    ) -> CheckInResponse:
        _require_agent(factory, agent_id, authorization)
        now = clock()
        with UnitOfWork(factory) as uow:
            _, metadata_changed = uow.agents.refresh_metadata(
                agent_id,
                AgentMetadata.model_validate(request.model_dump()),
                now=now,
            )
            if metadata_changed:
                uow.audit_events.add(
                    _audit_event(
                        AuditEventType.AGENT_METADATA_UPDATED,
                        occurred_at=now,
                        agent_id=agent_id,
                    )
                )
            uow.commit()
        return CheckInResponse(agent_id=agent_id)

    @router.post("/tasks", response_model=Task, status_code=201)
    def create_task(
        request: TaskCreateRequest,
        operator: Annotated[Operator, Depends(require_operator)],
        idempotency_key: Annotated[
            str | None,
            Header(
                alias="Idempotency-Key",
                min_length=16,
                max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
        ] = None,
    ) -> Task:
        now = clock()
        task = Task(
            id=TaskId(uuid4()),
            agent_id=request.agent_id,
            action=request.action,
            created_at=now,
            updated_at=now,
        )
        with UnitOfWork(factory) as uow:
            key_digest = (
                None if idempotency_key is None else digest_idempotency_key(idempotency_key)
            )
            if key_digest is not None:
                existing = uow.tasks.get_by_idempotency_key(operator.id, key_digest)
                if existing is not None:
                    if existing.agent_id != task.agent_id or existing.action != task.action:
                        raise HTTPException(
                            HTTPStatus.CONFLICT,
                            "idempotency key was already used for a different task",
                        )
                    return existing
            agent = uow.agents.get(request.agent_id)
            if agent is None:
                raise HTTPException(HTTPStatus.NOT_FOUND, "agent not found")
            if agent.status is not AgentStatus.ACTIVE:
                raise HTTPException(HTTPStatus.CONFLICT, "agent is disabled")
            created = True
            if key_digest is None:
                uow.tasks.add(task, created_by_operator_id=operator.id)
            else:
                task, created = uow.tasks.add_idempotent(
                    task,
                    operator_id=operator.id,
                    key_digest=key_digest,
                )
                if not created and (
                    task.agent_id != request.agent_id or task.action != request.action
                ):
                    raise HTTPException(
                        HTTPStatus.CONFLICT,
                        "idempotency key was already used for a different task",
                    )
            if not created:
                return task
            uow.audit_events.add(
                _audit_event(
                    AuditEventType.TASK_CREATED,
                    occurred_at=now,
                    operator_id=operator.id,
                    agent_id=task.agent_id,
                    task_id=task.id,
                )
            )
            uow.commit()
        return task

    @router.post("/agents/{agent_id}/disable", response_model=RegisteredAgent)
    def disable_agent_identity(
        agent_id: AgentId,
        operator: Annotated[Operator, Depends(require_operator)],
    ) -> RegisteredAgent:
        with UnitOfWork(factory) as uow:
            if not uow.agents.lock_for_update(agent_id):
                raise HTTPException(HTTPStatus.NOT_FOUND, "agent not found")
            agent = uow.agents.get(agent_id)
            assert agent is not None
            if agent.status is AgentStatus.DISABLED:
                return agent
            now = clock()
            disabled = disable_agent(agent, at=now)
            uow.agents.update_lifecycle(disabled)
            uow.audit_events.add(
                _audit_event(
                    AuditEventType.AGENT_DISABLED,
                    occurred_at=now,
                    operator_id=operator.id,
                    agent_id=agent.id,
                )
            )
            uow.commit()
            return disabled

    @router.get("/agents/{agent_id}/tasks/next", response_model=TaskResponse)
    def next_task(
        agent_id: AgentId,
        authorization: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
    ) -> TaskResponse:
        _require_agent(factory, agent_id, authorization)
        now = clock()
        with UnitOfWork(factory) as uow:
            if not uow.agents.lock_for_update(agent_id, active_only=True):
                raise unauthorized()
            task = uow.tasks.claim_next(
                agent_id,
                now=now,
                lease_duration=timedelta(seconds=settings.task_lease_seconds),
            )
            if task is None:
                return TaskResponse(task=None)
            uow.audit_events.add(
                _audit_event(
                    AuditEventType.TASK_CLAIMED,
                    occurred_at=now,
                    agent_id=agent_id,
                    task_id=task.id,
                )
            )
            uow.commit()
        return TaskResponse(task=task)

    @router.post("/agents/{agent_id}/results", response_model=ActionResult)
    def submit_result(
        agent_id: AgentId,
        submission: ActionResultSubmission,
        authorization: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
    ) -> ActionResult:
        _require_agent(factory, agent_id, authorization)
        result: ActionResult
        if isinstance(submission, ActionSuccessSubmission):
            result = ActionSuccess.model_validate(submission.model_dump())
        else:
            result = ActionFailure.model_validate(submission.model_dump())
        if result.agent_id != agent_id:
            raise HTTPException(HTTPStatus.BAD_REQUEST, "result agent does not match route")
        with UnitOfWork(factory) as uow:
            task = uow.tasks.get(result.task_id)
            if task is None or task.agent_id != agent_id:
                raise HTTPException(HTTPStatus.NOT_FOUND, "task not found")
            existing = uow.results.get(result.task_id)
            if existing is not None:
                if existing == result:
                    return existing
                raise HTTPException(HTTPStatus.CONFLICT, "task already has a result")
            if task.status is not TaskStatus.CLAIMED:
                raise HTTPException(HTTPStatus.CONFLICT, "task is not awaiting a result")
            now = clock()
            if task.lease_expires_at is None or task.lease_expires_at <= now:
                raise HTTPException(HTTPStatus.CONFLICT, "task lease has expired")
            allowed_skew = timedelta(seconds=settings.result_clock_skew_seconds)
            if (
                result.completed_at < task.updated_at - allowed_skew
                or result.completed_at > now + allowed_skew
            ):
                raise HTTPException(
                    HTTPStatus.BAD_REQUEST,
                    "result completion time is implausible",
                )
            if result.status == "completed" and result.output.kind != task.action.kind:
                raise HTTPException(HTTPStatus.BAD_REQUEST, "result output does not match action")
            running = transition_task(task, TaskStatus.RUNNING, at=now)
            final_status = (
                TaskStatus.COMPLETED if result.status == "completed" else TaskStatus.FAILED
            )
            finished = transition_task(running, final_status, at=now)
            if not uow.tasks.finish_claimed(finished, lease_valid_at=now):
                existing = uow.results.get(result.task_id)
                if existing == result:
                    return existing
                raise HTTPException(
                    HTTPStatus.CONFLICT,
                    "task is no longer awaiting a result",
                )
            if not uow.results.add(result, received_at=now):
                existing = uow.results.get(result.task_id)
                if existing == result:
                    return existing
                raise HTTPException(
                    HTTPStatus.CONFLICT,
                    "task already has a result",
                )
            event_type = (
                AuditEventType.TASK_COMPLETED
                if result.status == "completed"
                else AuditEventType.TASK_FAILED
            )
            uow.audit_events.add(
                _audit_event(
                    event_type,
                    occurred_at=now,
                    agent_id=agent_id,
                    task_id=task.id,
                )
            )
            uow.commit()
        return result

    return router


def create_app(
    settings: Settings | None = None,
    session_factory: SessionFactory | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    credential_factory: Callable[[], str] = generate_credential,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    owned_engine = None
    if session_factory is None:
        owned_engine = create_database_engine(resolved_settings.database_url)
        session_factory = create_session_factory(owned_engine)
    resolved_clock = clock or (lambda: datetime.now(UTC))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        yield
        if owned_engine is not None:
            owned_engine.dispose()

    app = FastAPI(title="c2-relay", version=__version__, lifespan=lifespan)
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)
    app.add_middleware(
        AuthenticationFailureLimitMiddleware,
        max_failures=resolved_settings.auth_failure_limit,
        window_seconds=resolved_settings.auth_failure_window_seconds,
    )
    app.include_router(
        _router(
            resolved_settings,
            session_factory,
            resolved_clock,
            credential_factory,
            OperatorService(session_factory, clock=resolved_clock),
        )
    )

    return app
