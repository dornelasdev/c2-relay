from datetime import UTC, datetime
from uuid import UUID

import httpx2
from pydantic import SecretStr

from c2_relay.agent.client import RelayClient
from c2_relay.models import (
    ActionSuccess,
    AgentId,
    AgentMetadata,
    HostnameOutput,
    TaskId,
)

AGENT_ID = AgentId(UUID("20000000-0000-4000-8000-000000000001"))
TASK_ID = TaskId(UUID("10000000-0000-4000-8000-000000000001"))
NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)


def test_relay_client_workflow_and_headers() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/enroll"):
            return httpx2.Response(
                201, json={"agent_id": str(AGENT_ID), "credential": "credential-" + "x" * 32}
            )
        if request.url.path.endswith("/check-ins"):
            return httpx2.Response(200, json={"agent_id": str(AGENT_ID), "accepted": True})
        if request.method == "GET":
            return httpx2.Response(200, json={"task": None})
        return httpx2.Response(
            200,
            json={
                "status": "completed",
                "task_id": str(TASK_ID),
                "agent_id": str(AGENT_ID),
                "completed_at": NOW.isoformat(),
                "output": {"kind": "host.hostname", "hostname": "host"},
            },
        )

    http = httpx2.Client(
        base_url="http://relay.test", transport=httpx2.MockTransport(handler), timeout=1
    )
    client = RelayClient("http://ignored", timeout=1, client=http)
    metadata = AgentMetadata(
        hostname="host", operating_system="Linux", username="user", agent_version="0.1.0"
    )
    enrollment = client.enroll(metadata, SecretStr("bootstrap"))
    credential = SecretStr(enrollment.credential)
    client.check_in(AGENT_ID, credential)
    assert client.next_task(AGENT_ID, credential) is None
    client.submit_result(
        AGENT_ID,
        credential,
        ActionSuccess(
            task_id=TASK_ID,
            agent_id=AGENT_ID,
            completed_at=NOW,
            output=HostnameOutput(hostname="host"),
        ),
    )
    client.close()

    assert requests[0].headers["x-bootstrap-token"] == "bootstrap"
    assert all(request.headers["authorization"].startswith("Bearer ") for request in requests[1:])


def test_relay_client_closes_an_owned_http_client() -> None:
    client = RelayClient("http://relay.test", timeout=1)
    client.close()
