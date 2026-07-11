"""FastAPI application factory and versioned routes."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import RequestResponseEndpoint

from c2_relay import __version__
from c2_relay.api.schemas import (
    ActionResultSubmission,
    ActionSuccessSubmission,
    CheckInResponse,
    EnrollmentRequest,
    EnrollmentResponse,
    HealthResponse,
    TaskCreateRequest,
    TaskResponse,
)
from c2_relay.core.config import Settings, get_settings
from c2_relay.core.security import (
    credential_matches,
    digest_credential,
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
    Task,
    TaskId,
    TaskStatus,
    transition_task,
)

MAX_REQUEST_BYTES = 64 * 1024
_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=HTTPStatus.UNAUTHORIZED,
        detail="invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _require_bootstrap(settings: Settings, provided: str | None) -> None:
    configured = settings.bootstrap_token
    if configured is None:
        raise HTTPException(HTTPStatus.SERVICE_UNAVAILABLE, "bootstrap operations are disabled")
    if provided is None or not secret_matches(provided, configured.get_secret_value()):
        raise _unauthorized()


def _require_agent(
    factory: SessionFactory,
    agent_id: AgentId,
    authorization: HTTPAuthorizationCredentials | None,
) -> None:
    if authorization is None or authorization.scheme.lower() != "bearer":
        raise _unauthorized()
    with UnitOfWork(factory) as uow:
        expected = uow.agents.credential_digest_for(agent_id)
        if expected is None or not credential_matches(authorization.credentials, expected):
            raise _unauthorized()


def _router(
    settings: Settings,
    factory: SessionFactory,
    clock: Callable[[], datetime],
    credential_factory: Callable[[], str],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @router.post("/agents/enroll", response_model=EnrollmentResponse, status_code=201)
    def enroll(
        request: EnrollmentRequest,
        bootstrap_token: str | None = Header(default=None, alias="X-Bootstrap-Token"),
    ) -> EnrollmentResponse:
        _require_bootstrap(settings, bootstrap_token)
        agent_id = AgentId(uuid4())
        credential = credential_factory()
        with UnitOfWork(factory) as uow:
            uow.agents.add(
                agent_id,
                request,
                digest_credential(credential),
                now=clock(),
            )
            uow.commit()
        return EnrollmentResponse(agent_id=agent_id, credential=credential)

    @router.post("/agents/{agent_id}/check-ins", response_model=CheckInResponse)
    def check_in(
        agent_id: AgentId,
        authorization: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
    ) -> CheckInResponse:
        _require_agent(factory, agent_id, authorization)
        with UnitOfWork(factory) as uow:
            uow.agents.touch(agent_id, now=clock())
            uow.commit()
        return CheckInResponse(agent_id=agent_id)

    @router.post("/tasks", response_model=Task, status_code=201)
    def create_task(
        request: TaskCreateRequest,
        bootstrap_token: str | None = Header(default=None, alias="X-Bootstrap-Token"),
    ) -> Task:
        _require_bootstrap(settings, bootstrap_token)
        now = clock()
        task = Task(
            id=TaskId(uuid4()),
            agent_id=request.agent_id,
            action=request.action,
            created_at=now,
            updated_at=now,
        )
        with UnitOfWork(factory) as uow:
            if uow.agents.get(request.agent_id) is None:
                raise HTTPException(HTTPStatus.NOT_FOUND, "agent not found")
            uow.tasks.add(task)
            uow.commit()
        return task

    @router.get("/agents/{agent_id}/tasks/next", response_model=TaskResponse)
    def next_task(
        agent_id: AgentId,
        authorization: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
    ) -> TaskResponse:
        _require_agent(factory, agent_id, authorization)
        with UnitOfWork(factory) as uow:
            task = uow.tasks.next_queued(agent_id)
            if task is None:
                return TaskResponse(task=None)
            claimed = transition_task(task, TaskStatus.CLAIMED, at=clock())
            uow.tasks.update(claimed)
            uow.commit()
        return TaskResponse(task=claimed)

    @router.post("/agents/{agent_id}/results", response_model=ActionResult)
    def submit_result(
        agent_id: AgentId,
        submission: ActionResultSubmission,
        authorization: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
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
            if result.status == "completed" and result.output.kind != task.action.kind:
                raise HTTPException(HTTPStatus.BAD_REQUEST, "result output does not match action")
            now = clock()
            running = transition_task(task, TaskStatus.RUNNING, at=now)
            final_status = (
                TaskStatus.COMPLETED if result.status == "completed" else TaskStatus.FAILED
            )
            finished = transition_task(running, final_status, at=now)
            uow.tasks.update(finished)
            uow.results.add(result)
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

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        yield
        if owned_engine is not None:
            owned_engine.dispose()

    app = FastAPI(title="c2-relay", version=__version__, lifespan=lifespan)
    app.include_router(
        _router(
            resolved_settings,
            session_factory,
            clock or (lambda: datetime.now(UTC)),
            credential_factory,
        )
    )

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length = request.headers.get("content-length")
        if (
            content_length is not None
            and content_length.isdecimal()
            and int(content_length) > MAX_REQUEST_BYTES
        ):
            return JSONResponse(
                status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "request body too large"},
            )
        return await call_next(request)

    return app
