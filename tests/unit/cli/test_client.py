from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from c2_relay.api.schemas import EnrollmentGrantCreateRequest
from c2_relay.cli.client import OperatorClient
from c2_relay.models import EnrollmentGrant, EnrollmentGrantId, OperatorId

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
GRANT_ID = EnrollmentGrantId(UUID("50000000-0000-4000-8000-000000000001"))
OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))
CREDENTIAL = SecretStr(f"c2o.{OPERATOR_ID}.{'o' * 32}")
TOKEN = f"c2g.{GRANT_ID}.{'g' * 32}"
GRANT = EnrollmentGrant(
    id=GRANT_ID,
    issued_by_operator_id=OPERATOR_ID,
    created_at=NOW,
    expires_at=NOW + timedelta(minutes=15),
)


def test_operator_client_issues_revokes_and_uses_only_operator_authentication() -> None:
    requests: list[httpx2.Request] = []
    revoked = GRANT.model_copy(update={"revoked_at": NOW, "revoked_by_operator_id": OPERATOR_ID})

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/revoke"):
            return httpx2.Response(200, json=revoked.model_dump(mode="json"))
        return httpx2.Response(201, json={"grant": GRANT.model_dump(mode="json"), "token": TOKEN})

    with httpx2.Client(
        base_url="http://relay.test", transport=httpx2.MockTransport(handler)
    ) as http:
        client = OperatorClient("http://relay.test", CREDENTIAL, timeout=1, client=http)
        issued = client.issue_grant(EnrollmentGrantCreateRequest())
        assert issued.grant == GRANT
        assert issued.token == TOKEN
        assert client.revoke_grant(GRANT_ID) == revoked
        client.close()
        assert not http.is_closed

    assert [request.url.path for request in requests] == [
        "/api/v1/enrollment-grants",
        f"/api/v1/enrollment-grants/{GRANT_ID}/revoke",
    ]
    assert requests[0].read() == b'{"expires_in_seconds":900}'
    for request in requests:
        assert request.method == "POST"
        assert request.headers["authorization"] == f"Bearer {CREDENTIAL.get_secret_value()}"
        assert "x-bootstrap-token" not in request.headers
        assert "x-enrollment-token" not in request.headers


def test_operator_client_closes_owned_client() -> None:
    client = OperatorClient("https://relay.test", CREDENTIAL, timeout=1)
    client.close()


@pytest.mark.parametrize(
    "url",
    [
        "relay.test",
        "ftp://relay.test",
        "http:///",
        "http://user:password@relay.test",
        "http://relay.test?secret=value",
        "http://relay.test#fragment",
        "http://relay.test/prefix",
        "http://relay.test:invalid",
    ],
)
def test_operator_client_rejects_invalid_or_ambiguous_origins(url: str) -> None:
    with pytest.raises((ValueError, httpx2.InvalidURL)):
        OperatorClient(url, CREDENTIAL, timeout=1)


@pytest.mark.parametrize("operation", ["issue", "revoke"])
@pytest.mark.parametrize("status", [401, 404, 409, 422, 429, 503])
def test_operator_client_rejects_failed_http_responses(operation: str, status: int) -> None:
    with httpx2.Client(
        base_url="http://relay.test",
        transport=httpx2.MockTransport(lambda request: httpx2.Response(status, json={})),
    ) as http:
        client = OperatorClient("http://relay.test", CREDENTIAL, timeout=1, client=http)
        with pytest.raises(httpx2.HTTPStatusError) as error:
            if operation == "issue":
                client.issue_grant(EnrollmentGrantCreateRequest())
            else:
                client.revoke_grant(GRANT_ID)
        assert error.value.response.status_code == status


@pytest.mark.parametrize("operation", ["issue", "revoke"])
def test_operator_client_rejects_malformed_success_responses(operation: str) -> None:
    with httpx2.Client(
        base_url="http://relay.test",
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={})),
    ) as http:
        client = OperatorClient("http://relay.test", CREDENTIAL, timeout=1, client=http)
        with pytest.raises(ValidationError):
            if operation == "issue":
                client.issue_grant(EnrollmentGrantCreateRequest())
            else:
                client.revoke_grant(GRANT_ID)


@pytest.mark.parametrize("operation", ["issue", "revoke"])
def test_operator_client_does_not_follow_redirects_even_with_injected_client(
    operation: str,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(307, headers={"Location": "http://other.test/collect"})

    with httpx2.Client(
        base_url="http://relay.test", transport=httpx2.MockTransport(handler), follow_redirects=True
    ) as http:
        client = OperatorClient("http://relay.test", CREDENTIAL, timeout=1, client=http)
        with pytest.raises(httpx2.HTTPStatusError):
            if operation == "issue":
                client.issue_grant(EnrollmentGrantCreateRequest())
            else:
                client.revoke_grant(GRANT_ID)
    assert len(requests) == 1
    assert requests[0].url.host == "relay.test"
