"""Validated HTTP client for agent-server communication."""

import httpx2
from pydantic import SecretStr, TypeAdapter

from c2_relay.api.schemas import CheckInResponse, EnrollmentResponse, TaskResponse
from c2_relay.models import ActionResult, AgentId, AgentMetadata, Task

_RESULT_ADAPTER: TypeAdapter[ActionResult] = TypeAdapter(ActionResult)


class RelayClient:
    def __init__(
        self,
        server_url: str,
        *,
        timeout: float,
        client: httpx2.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx2.Client(base_url=server_url, timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def enroll(self, metadata: AgentMetadata, bootstrap_token: SecretStr) -> EnrollmentResponse:
        response = self._client.post(
            "/api/v1/agents/enroll",
            headers={"X-Bootstrap-Token": bootstrap_token.get_secret_value()},
            json=metadata.model_dump(mode="json"),
        )
        response.raise_for_status()
        return EnrollmentResponse.model_validate_json(response.content)

    def check_in(self, agent_id: AgentId, credential: SecretStr) -> CheckInResponse:
        response = self._client.post(
            f"/api/v1/agents/{agent_id}/check-ins",
            headers=self._authorization(credential),
        )
        response.raise_for_status()
        return CheckInResponse.model_validate_json(response.content)

    def next_task(self, agent_id: AgentId, credential: SecretStr) -> Task | None:
        response = self._client.get(
            f"/api/v1/agents/{agent_id}/tasks/next",
            headers=self._authorization(credential),
        )
        response.raise_for_status()
        return TaskResponse.model_validate_json(response.content).task

    def submit_result(self, agent_id: AgentId, credential: SecretStr, result: ActionResult) -> None:
        response = self._client.post(
            f"/api/v1/agents/{agent_id}/results",
            headers=self._authorization(credential),
            json=result.model_dump(mode="json"),
        )
        response.raise_for_status()
        _RESULT_ADAPTER.validate_json(response.content)

    @staticmethod
    def _authorization(credential: SecretStr) -> dict[str, str]:
        return {"Authorization": f"Bearer {credential.get_secret_value()}"}
