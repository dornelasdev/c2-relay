"""Validated HTTP client for operator management and inspection."""

from uuid import UUID

import httpx2
from pydantic import SecretStr

from c2_relay.api.schemas import (
    AgentListResponse,
    AuditEventListResponse,
    EnrollmentGrantCreateRequest,
    EnrollmentGrantIssueResponse,
    StoredResultResponse,
    TaskListResponse,
)
from c2_relay.models import (
    AgentId,
    AgentStatus,
    AuditEventType,
    EnrollmentGrant,
    EnrollmentGrantId,
    OperatorId,
    RegisteredAgent,
    Task,
    TaskId,
    TaskStatus,
)


def normalize_server_url(server_url: str) -> str:
    try:
        url = httpx2.URL(server_url)
    except httpx2.InvalidURL:
        raise ValueError("operator server URL must be a valid HTTP(S) origin") from None
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError("operator server URL must be an HTTP(S) origin without credentials")
    if url.port == {"http": 80, "https": 443}[url.scheme]:
        url = url.copy_with(port=None)
    return str(url).rstrip("/")


class OperatorClient:
    def __init__(
        self,
        server_url: str,
        credential: SecretStr,
        *,
        timeout: float,
        client: httpx2.Client | None = None,
    ) -> None:
        server_url = normalize_server_url(server_url)
        self._credential = credential
        self._owns_client = client is None
        self._client = client or httpx2.Client(
            base_url=server_url, timeout=timeout, follow_redirects=False
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def issue_grant(self, request: EnrollmentGrantCreateRequest) -> EnrollmentGrantIssueResponse:
        response = self._client.post(
            "/api/v1/enrollment-grants",
            headers=self._authorization(),
            json=request.model_dump(mode="json"),
            follow_redirects=False,
        )
        response.raise_for_status()
        return EnrollmentGrantIssueResponse.model_validate_json(response.content)

    def revoke_grant(self, grant_id: EnrollmentGrantId) -> EnrollmentGrant:
        response = self._client.post(
            f"/api/v1/enrollment-grants/{grant_id}/revoke",
            headers=self._authorization(),
            follow_redirects=False,
        )
        response.raise_for_status()
        return EnrollmentGrant.model_validate_json(response.content)

    def list_agents(
        self, *, status: AgentStatus | None = None, limit: int = 50, offset: int = 0
    ) -> AgentListResponse:
        return AgentListResponse.model_validate_json(
            self._get("/api/v1/agents", status=status, limit=limit, offset=offset)
        )

    def get_agent(self, agent_id: AgentId) -> RegisteredAgent:
        return RegisteredAgent.model_validate_json(self._get(f"/api/v1/agents/{agent_id}"))

    def list_tasks(
        self,
        *,
        agent_id: AgentId | None = None,
        status: TaskStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> TaskListResponse:
        return TaskListResponse.model_validate_json(
            self._get("/api/v1/tasks", agent_id=agent_id, status=status, limit=limit, offset=offset)
        )

    def get_task(self, task_id: TaskId) -> Task:
        return Task.model_validate_json(self._get(f"/api/v1/tasks/{task_id}"))

    def get_task_result(self, task_id: TaskId) -> StoredResultResponse:
        return StoredResultResponse.model_validate_json(
            self._get(f"/api/v1/tasks/{task_id}/result")
        )

    def list_audit_events(
        self,
        *,
        event_type: AuditEventType | None = None,
        operator_id: OperatorId | None = None,
        agent_id: AgentId | None = None,
        task_id: TaskId | None = None,
        enrollment_grant_id: EnrollmentGrantId | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AuditEventListResponse:
        return AuditEventListResponse.model_validate_json(
            self._get(
                "/api/v1/audit-events",
                event_type=event_type,
                operator_id=operator_id,
                agent_id=agent_id,
                task_id=task_id,
                enrollment_grant_id=enrollment_grant_id,
                limit=limit,
                offset=offset,
            )
        )

    def _get(self, path: str, **params: str | int | UUID | None) -> bytes:
        response = self._client.get(
            path,
            headers=self._authorization(),
            params={key: str(value) for key, value in params.items() if value is not None},
            follow_redirects=False,
        )
        response.raise_for_status()
        return response.content

    def _authorization(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._credential.get_secret_value()}"}
