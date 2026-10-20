"""Validated HTTP client for operator enrollment workflows."""

import httpx2
from pydantic import SecretStr

from c2_relay.api.schemas import EnrollmentGrantCreateRequest, EnrollmentGrantIssueResponse
from c2_relay.models import EnrollmentGrant, EnrollmentGrantId


class OperatorClient:
    def __init__(
        self,
        server_url: str,
        credential: SecretStr,
        *,
        timeout: float,
        client: httpx2.Client | None = None,
    ) -> None:
        url = httpx2.URL(server_url)
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

    def _authorization(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._credential.get_secret_value()}"}
