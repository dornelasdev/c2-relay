from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from c2_relay.api import create_app
from c2_relay.core.config import Settings
from c2_relay.db.schema import Base
from c2_relay.db.session import create_database_engine, create_session_factory
from c2_relay.services.operators import OperatorService

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
BOOTSTRAP_TOKEN = "b" * 32
OPERATOR_UUID = UUID("30000000-0000-4000-8000-000000000001")
OPERATOR_CREDENTIAL = f"c2o.{OPERATOR_UUID}.{'o' * 32}"
AGENT_METADATA = {
    "hostname": "relay-host",
    "operating_system": "Linux",
    "username": "operator",
    "agent_version": "0.1.0",
    "architecture": "x86_64",
}


class MutableClock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def client(tmp_path: Path, clock: MutableClock) -> Iterator[TestClient]:
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'api.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    OperatorService(
        factory,
        clock=clock,
        id_factory=lambda: OPERATOR_UUID,
        credential_factory=lambda operator_id: SecretStr(OPERATOR_CREDENTIAL),
    ).provision("API Operator")
    settings = Settings(
        database_url=str(engine.url),
        bootstrap_token=SecretStr(BOOTSTRAP_TOKEN),
    )
    app = create_app(
        settings,
        factory,
        clock=clock,
        credential_factory=lambda: "agent-credential-" + "x" * 32,
    )
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()


def bootstrap_headers(token: str = BOOTSTRAP_TOKEN) -> dict[str, str]:
    return {"X-Bootstrap-Token": token}


def enroll(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/v1/agents/enroll",
        headers=bootstrap_headers(),
        json=AGENT_METADATA,
    )
    assert response.status_code == 201
    payload = response.json()
    return payload["agent_id"], payload["credential"]


def agent_headers(credential: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {credential}"}


def operator_headers(credential: str = OPERATOR_CREDENTIAL) -> dict[str, str]:
    return {"Authorization": f"Bearer {credential}"}


def create_task(client: TestClient, agent_id: str, kind: str = "host.hostname") -> dict[str, Any]:
    response = client.post(
        "/api/v1/tasks",
        headers=operator_headers(),
        json={"agent_id": agent_id, "action": {"kind": kind}},
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def claim_task(client: TestClient, agent_id: str, credential: str) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/agents/{agent_id}/tasks/next",
        headers=agent_headers(credential),
    )
    assert response.status_code == 200
    task = response.json()["task"]
    assert task is not None
    return cast(dict[str, Any], task)


def test_health_is_public(client: TestClient) -> None:
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_enrollment_requires_the_configured_bootstrap_token(client: TestClient) -> None:
    for headers in ({}, bootstrap_headers("x" * 32)):
        response = client.post("/api/v1/agents/enroll", headers=headers, json=AGENT_METADATA)
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid credentials"}
        assert response.headers["www-authenticate"] == "Bearer"


def test_bootstrap_operations_can_be_disabled(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'disabled.db'}")
    Base.metadata.create_all(engine)
    app = create_app(Settings(bootstrap_token=None), create_session_factory(engine))

    with TestClient(app) as client:
        response = client.post("/api/v1/agents/enroll", json=AGENT_METADATA)

    assert response.status_code == 503
    assert response.json() == {"detail": "bootstrap operations are disabled"}
    engine.dispose()


def test_enrollment_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post(
        "/api/v1/agents/enroll",
        headers=bootstrap_headers(),
        json={**AGENT_METADATA, "ip_address": "192.0.2.1"},
    )

    assert response.status_code == 422


def test_agent_check_in_requires_its_own_bearer_credential(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    route = f"/api/v1/agents/{agent_id}/check-ins"

    assert client.post(route).status_code == 401
    assert client.post(route, headers=agent_headers("wrong")).status_code == 401
    assert client.post(route, headers={"Authorization": "Basic abc"}).status_code == 401

    response = client.post(route, headers=agent_headers(credential))
    assert response.status_code == 200
    assert response.json() == {"agent_id": agent_id, "accepted": True}


def test_task_creation_rejects_an_unknown_agent(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tasks",
        headers=operator_headers(),
        json={
            "agent_id": "00000000-0000-0000-0000-000000000000",
            "action": {"kind": "host.hostname"},
        },
    )

    assert response.status_code == 404


def test_task_creation_requires_an_active_operator_credential(client: TestClient) -> None:
    agent_id, _ = enroll(client)
    route = "/api/v1/tasks"
    payload = {"agent_id": agent_id, "action": {"kind": "host.hostname"}}
    unknown_id = UUID("30000000-0000-4000-8000-000000000099")
    invalid_headers: tuple[dict[str, str], ...] = (
        {},
        bootstrap_headers(),
        operator_headers("not-an-operator-credential"),
        operator_headers(f"c2o.{OPERATOR_UUID}.{'z' * 32}"),
        operator_headers(f"c2o.{unknown_id}.{'z' * 32}"),
        {"Authorization": "Basic abc"},
    )

    for headers in invalid_headers:
        response = client.post(route, headers=headers, json=payload)
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid credentials"}
        assert response.headers["www-authenticate"] == "Bearer"

    assert client.post(route, headers=operator_headers(), json=payload).status_code == 201


def test_operator_can_disable_an_agent_and_revoke_its_access(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    disable_route = f"/api/v1/agents/{agent_id}/disable"

    assert client.post(disable_route).status_code == 401
    assert client.post(disable_route, headers=agent_headers(credential)).status_code == 401

    response = client.post(disable_route, headers=operator_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
    assert response.json()["disabled_at"] == NOW.isoformat().replace("+00:00", "Z")

    repeated = client.post(disable_route, headers=operator_headers())
    assert repeated.status_code == 200
    assert repeated.json() == response.json()

    check_in = client.post(
        f"/api/v1/agents/{agent_id}/check-ins",
        headers=agent_headers(credential),
    )
    assert check_in.status_code == 401
    assert check_in.json() == {"detail": "invalid credentials"}

    task = client.post(
        "/api/v1/tasks",
        headers=operator_headers(),
        json={"agent_id": agent_id, "action": {"kind": "host.hostname"}},
    )
    assert task.status_code == 409
    assert task.json() == {"detail": "agent is disabled"}


def test_disabling_an_unknown_agent_returns_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/agents/00000000-0000-0000-0000-000000000000/disable",
        headers=operator_headers(),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "agent not found"}


def test_polling_claims_oldest_task_once(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    created = create_task(client, agent_id)

    claimed = claim_task(client, agent_id, credential)
    assert claimed["id"] == created["id"]
    assert claimed["status"] == "claimed"
    assert claimed["lease_expires_at"] is not None

    empty = client.get(
        f"/api/v1/agents/{agent_id}/tasks/next",
        headers=agent_headers(credential),
    )
    assert empty.json() == {"task": None}


def test_expired_claim_is_rejected_then_recovered(
    client: TestClient,
    clock: MutableClock,
) -> None:
    agent_id, credential = enroll(client)
    create_task(client, agent_id)
    claimed = claim_task(client, agent_id, credential)
    clock.advance(timedelta(seconds=31))
    result = {
        "status": "completed",
        "task_id": claimed["id"],
        "agent_id": agent_id,
        "completed_at": NOW.isoformat(),
        "output": {"kind": "host.hostname", "hostname": "relay-host"},
    }

    expired = client.post(
        f"/api/v1/agents/{agent_id}/results",
        headers=agent_headers(credential),
        json=result,
    )
    assert expired.status_code == 409
    assert expired.json() == {"detail": "task lease has expired"}

    recovered = claim_task(client, agent_id, credential)
    assert recovered["id"] == claimed["id"]
    assert recovered["lease_expires_at"] != claimed["lease_expires_at"]


def test_completed_result_is_persisted_and_idempotent(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    create_task(client, agent_id)
    task = claim_task(client, agent_id, credential)
    result = {
        "status": "completed",
        "task_id": task["id"],
        "agent_id": agent_id,
        "completed_at": NOW.isoformat(),
        "output": {"kind": "host.hostname", "hostname": "relay-host"},
    }
    route = f"/api/v1/agents/{agent_id}/results"

    first = client.post(route, headers=agent_headers(credential), json=result)
    second = client.post(route, headers=agent_headers(credential), json=result)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()

    changed = {**result, "completed_at": (NOW + timedelta(seconds=1)).isoformat()}
    assert client.post(route, headers=agent_headers(credential), json=changed).status_code == 409


def test_failed_result_is_accepted(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    create_task(client, agent_id)
    task = claim_task(client, agent_id, credential)
    result = {
        "status": "failed",
        "task_id": task["id"],
        "agent_id": agent_id,
        "completed_at": NOW.isoformat(),
        "error": {"code": "action_failed", "message": "failure", "retryable": False},
    }

    response = client.post(
        f"/api/v1/agents/{agent_id}/results",
        headers=agent_headers(credential),
        json=result,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"


def test_result_must_match_agent_task_state_and_action(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    created = create_task(client, agent_id)
    base = {
        "status": "completed",
        "task_id": created["id"],
        "agent_id": agent_id,
        "completed_at": NOW.isoformat(),
        "output": {"kind": "host.hostname", "hostname": "relay-host"},
    }
    route = f"/api/v1/agents/{agent_id}/results"
    headers = agent_headers(credential)

    assert client.post(route, headers=headers, json=base).status_code == 409
    claim_task(client, agent_id, credential)

    wrong_agent = {**base, "agent_id": str(UUID(int=0))}
    assert client.post(route, headers=headers, json=wrong_agent).status_code == 400

    missing_task = {**base, "task_id": str(UUID(int=0))}
    assert client.post(route, headers=headers, json=missing_task).status_code == 404

    wrong_output = {
        **base,
        "output": {"kind": "host.current_user", "username": "operator"},
    }
    assert client.post(route, headers=headers, json=wrong_output).status_code == 400


def test_request_size_is_bounded(client: TestClient) -> None:
    response = client.post(
        "/api/v1/agents/enroll",
        headers={**bootstrap_headers(), "Content-Length": str(65 * 1024)},
        content=b"{}",
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}
