from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from c2_relay.api.schemas import EnrollmentGrantCreateRequest
from c2_relay.cli.client import OperatorClient
from c2_relay.models import (
    ActionSuccess,
    AgentId,
    AgentMetadata,
    AgentStatus,
    AuditEventType,
    EnrollmentGrant,
    EnrollmentGrantId,
    HostnameAction,
    HostnameOutput,
    OperatorId,
    RegisteredAgent,
    StoredActionResult,
    Task,
    TaskId,
    TaskStatus,
)

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
GRANT_ID = EnrollmentGrantId(UUID("50000000-0000-4000-8000-000000000001"))
OPERATOR_ID = OperatorId(UUID("30000000-0000-4000-8000-000000000001"))
AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))
TASK_ID = TaskId(UUID("10000000-0000-4000-8000-000000000001"))
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


def test_operator_client_reads_typed_resources_and_encodes_filters() -> None:
    agent = RegisteredAgent(
        id=AGENT_ID,
        metadata=AgentMetadata(
            hostname="read-test", operating_system="Linux", username="user", agent_version="0.3.0"
        ),
        created_at=NOW,
        last_seen_at=NOW,
    )
    task = Task(
        id=TASK_ID,
        agent_id=AGENT_ID,
        action=HostnameAction(),
        status=TaskStatus.COMPLETED,
        created_at=NOW,
        updated_at=NOW,
    )
    stored = StoredActionResult(
        result=ActionSuccess(
            task_id=TASK_ID,
            agent_id=AGENT_ID,
            completed_at=NOW,
            output=HostnameOutput(hostname="read-test"),
        ),
        received_at=NOW,
    )
    payloads = {
        "/api/v1/agents": {
            "items": [agent.model_dump(mode="json")],
            "total": 2,
            "limit": 2,
            "offset": 1,
        },
        "/api/v1/tasks": {
            "items": [task.model_dump(mode="json")],
            "total": 3,
            "limit": 3,
            "offset": 2,
        },
        f"/api/v1/tasks/{TASK_ID}": task.model_dump(mode="json"),
        f"/api/v1/tasks/{TASK_ID}/result": {"item": stored.model_dump(mode="json")},
        "/api/v1/audit-events": {"items": [], "total": 0, "limit": 4, "offset": 3},
    }
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=payloads[request.url.path])

    with httpx2.Client(
        base_url="http://relay.test", transport=httpx2.MockTransport(handler)
    ) as http:
        client = OperatorClient("http://relay.test", CREDENTIAL, timeout=1, client=http)
        assert client.list_agents(status=AgentStatus.ACTIVE, limit=2, offset=1).items == (agent,)
        assert client.list_tasks(
            agent_id=AGENT_ID, status=TaskStatus.COMPLETED, limit=3, offset=2
        ).items == (task,)
        assert client.get_task(TASK_ID) == task
        assert client.get_task_result(TASK_ID).item == stored
        assert (
            client.list_audit_events(
                event_type=AuditEventType.TASK_CREATED,
                operator_id=OPERATOR_ID,
                agent_id=AGENT_ID,
                task_id=TASK_ID,
                enrollment_grant_id=GRANT_ID,
                limit=4,
                offset=3,
            ).items
            == ()
        )

    assert dict(requests[0].url.params) == {"status": "active", "limit": "2", "offset": "1"}
    assert dict(requests[1].url.params) == {
        "agent_id": str(AGENT_ID),
        "status": "completed",
        "limit": "3",
        "offset": "2",
    }
    assert dict(requests[2].url.params) == dict(requests[3].url.params) == {}
    assert dict(requests[4].url.params) == {
        "event_type": "task.created",
        "operator_id": str(OPERATOR_ID),
        "agent_id": str(AGENT_ID),
        "task_id": str(TASK_ID),
        "enrollment_grant_id": str(GRANT_ID),
        "limit": "4",
        "offset": "3",
    }
    for request in requests:
        assert request.method == "GET"
        assert request.headers["authorization"] == f"Bearer {CREDENTIAL.get_secret_value()}"
        assert CREDENTIAL.get_secret_value() not in str(request.url)
        assert "x-bootstrap-token" not in request.headers
        assert "x-enrollment-token" not in request.headers


@pytest.mark.parametrize("method", ["list_agents", "list_tasks", "list_audit_events"])
def test_operator_client_list_defaults_omit_unset_filters(method: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert dict(request.url.params) == {"limit": "50", "offset": "0"}
        return httpx2.Response(200, json={"items": [], "total": 0, "limit": 50, "offset": 0})

    with httpx2.Client(
        base_url="http://relay.test", transport=httpx2.MockTransport(handler)
    ) as http:
        client = OperatorClient("http://relay.test", CREDENTIAL, timeout=1, client=http)
        assert getattr(client, method)().items == ()


@pytest.mark.parametrize(
    "method, args",
    [
        ("list_agents", ()),
        ("list_tasks", ()),
        ("get_task", (TASK_ID,)),
        ("get_task_result", (TASK_ID,)),
        ("list_audit_events", ()),
    ],
)
def test_operator_client_read_methods_reject_invalid_response_models(
    method: str, args: tuple[object, ...]
) -> None:
    with httpx2.Client(
        base_url="http://relay.test",
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={})),
    ) as http:
        client = OperatorClient("http://relay.test", CREDENTIAL, timeout=1, client=http)
        with pytest.raises(ValidationError):
            getattr(client, method)(*args)


@pytest.mark.parametrize("status", [401, 404, 429, 503])
def test_operator_client_read_requests_raise_on_http_errors(status: int) -> None:
    with httpx2.Client(
        base_url="http://relay.test",
        transport=httpx2.MockTransport(lambda request: httpx2.Response(status, json={})),
    ) as http:
        client = OperatorClient("http://relay.test", CREDENTIAL, timeout=1, client=http)
        with pytest.raises(httpx2.HTTPStatusError) as error:
            client.get_task_result(TASK_ID)
        assert error.value.response.status_code == status


def test_operator_client_read_requests_refuse_redirects() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(307, headers={"Location": "http://other.test/collect"})

    with httpx2.Client(
        base_url="http://relay.test", transport=httpx2.MockTransport(handler), follow_redirects=True
    ) as http:
        client = OperatorClient("http://relay.test", CREDENTIAL, timeout=1, client=http)
        with pytest.raises(httpx2.HTTPStatusError):
            client.list_agents()
    assert len(requests) == 1
    assert requests[0].url.host == "relay.test"
