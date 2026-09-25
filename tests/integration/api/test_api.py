from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from c2_relay.api import create_app
from c2_relay.core.config import Settings
from c2_relay.core.security import IdempotencyKeyDigest
from c2_relay.db.repositories import (
    AgentRepository,
    AuditRepository,
    ResultRepository,
    TaskRepository,
)
from c2_relay.db.schema import Base
from c2_relay.db.session import create_database_engine, create_session_factory
from c2_relay.models import (
    ActionResult,
    ActionSuccess,
    AgentId,
    HostnameOutput,
    OperatorId,
    Task,
    TaskId,
)
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
    credential_sequence = count(1)
    app = create_app(
        settings,
        factory,
        clock=clock,
        credential_factory=lambda: f"agent-credential-{next(credential_sequence):032d}",
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


def test_repeated_authentication_failures_are_rate_limited(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'rate-limit.db'}")
    Base.metadata.create_all(engine)
    settings = Settings(
        database_url=str(engine.url),
        auth_failure_limit=2,
        auth_failure_window_seconds=60,
    )
    credential = "do-not-log-this-credential"

    with (
        TestClient(create_app(settings, create_session_factory(engine))) as limited_client,
        caplog.at_level("WARNING", logger="c2_relay.api.middleware"),
    ):
        first = limited_client.get(
            "/api/v1/tasks",
            headers=operator_headers(credential),
        )
        second = limited_client.get(
            "/api/v1/tasks",
            headers=operator_headers(credential),
        )
        blocked = limited_client.get(
            "/api/v1/tasks",
            headers=operator_headers(credential),
        )
        health = limited_client.get("/api/v1/health")

    assert first.status_code == 401
    assert second.status_code == 401
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "too many authentication failures"}
    assert blocked.headers["retry-after"] == "60"
    assert health.status_code == 200
    assert credential not in caplog.text
    engine.dispose()


def test_operator_read_endpoints_reject_agent_credentials(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    task = create_task(client, agent_id)
    routes = (
        "/api/v1/agents",
        "/api/v1/audit-events",
        "/api/v1/tasks",
        f"/api/v1/tasks/{task['id']}",
        f"/api/v1/tasks/{task['id']}/result",
    )

    for route in routes:
        assert client.get(route).status_code == 401
        assert client.get(route, headers=agent_headers(credential)).status_code == 401


def test_operator_can_filter_bounded_non_sensitive_audit_history(
    client: TestClient,
    clock: MutableClock,
) -> None:
    agent_id, credential = enroll(client)
    refreshed_metadata = {**AGENT_METADATA, "hostname": "renamed-host"}
    clock.advance(timedelta(seconds=1))
    check_in_route = f"/api/v1/agents/{agent_id}/check-ins"
    assert (
        client.post(
            check_in_route,
            headers=agent_headers(credential),
            json=refreshed_metadata,
        ).status_code
        == 200
    )
    clock.advance(timedelta(seconds=1))
    assert (
        client.post(
            check_in_route,
            headers=agent_headers(credential),
            json=refreshed_metadata,
        ).status_code
        == 200
    )

    clock.advance(timedelta(seconds=1))
    first = create_task(client, agent_id)
    clock.advance(timedelta(seconds=1))
    claimed = claim_task(client, agent_id, credential)
    clock.advance(timedelta(seconds=1))
    result = {
        "status": "completed",
        "task_id": claimed["id"],
        "agent_id": agent_id,
        "completed_at": clock.current.isoformat(),
        "output": {"kind": "host.hostname", "hostname": "renamed-host"},
    }
    result_route = f"/api/v1/agents/{agent_id}/results"
    first_result = client.post(result_route, headers=agent_headers(credential), json=result)
    repeated_result = client.post(result_route, headers=agent_headers(credential), json=result)
    assert first_result.status_code == 200
    assert repeated_result.status_code == 200

    clock.advance(timedelta(seconds=1))
    second = create_task(client, agent_id)
    clock.advance(timedelta(seconds=1))
    cancel_route = f"/api/v1/tasks/{second['id']}/cancel"
    assert client.post(cancel_route, headers=operator_headers()).status_code == 200
    assert client.post(cancel_route, headers=operator_headers()).status_code == 200
    clock.advance(timedelta(seconds=1))
    disable_route = f"/api/v1/agents/{agent_id}/disable"
    assert client.post(disable_route, headers=operator_headers()).status_code == 200
    assert client.post(disable_route, headers=operator_headers()).status_code == 200

    response = client.get(
        "/api/v1/audit-events",
        headers=operator_headers(),
        params={"agent_id": agent_id},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["total"] == 8
    assert [event["event_type"] for event in payload["items"]] == [
        "agent.disabled",
        "task.cancelled",
        "task.created",
        "task.completed",
        "task.claimed",
        "task.created",
        "agent.metadata_updated",
        "agent.enrolled",
    ]
    assert all(event["agent_id"] == agent_id for event in payload["items"])
    assert "credential" not in response.text
    assert "digest" not in response.text
    assert "renamed-host" not in response.text
    assert '"output"' not in response.text

    operator_events = client.get(
        "/api/v1/audit-events",
        headers=operator_headers(),
        params={"operator_id": str(OPERATOR_UUID)},
    ).json()
    assert operator_events["total"] == 4
    task_events = client.get(
        "/api/v1/audit-events",
        headers=operator_headers(),
        params={"task_id": first["id"]},
    ).json()
    assert task_events["total"] == 3
    completed = client.get(
        "/api/v1/audit-events",
        headers=operator_headers(),
        params={"event_type": "task.completed", "limit": 1, "offset": 0},
    ).json()
    assert completed["total"] == 1
    assert completed["items"][0]["task_id"] == first["id"]
    assert (
        client.get(
            "/api/v1/audit-events",
            headers=operator_headers(),
            params={"limit": 101},
        ).status_code
        == 422
    )


def test_audit_failure_rolls_back_the_recorded_operation(client: TestClient) -> None:
    agent_id, _ = enroll(client)

    with (
        patch.object(AuditRepository, "add", side_effect=RuntimeError("audit unavailable")),
        pytest.raises(RuntimeError, match="audit unavailable"),
    ):
        create_task(client, agent_id)

    tasks = client.get("/api/v1/tasks", headers=operator_headers()).json()
    assert tasks["total"] == 0


def test_operator_can_list_and_filter_agents_without_credentials(client: TestClient) -> None:
    active_id, _ = enroll(client)
    disabled_id, _ = enroll(client)
    client.post(
        f"/api/v1/agents/{disabled_id}/disable",
        headers=operator_headers(),
    )

    response = client.get(
        "/api/v1/agents",
        headers=operator_headers(),
        params={"status": "active", "limit": 1, "offset": 0},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] == 1
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert [item["id"] for item in payload["items"]] == [active_id]
    assert "credential" not in response.text
    assert "digest" not in response.text

    disabled = client.get(
        "/api/v1/agents",
        headers=operator_headers(),
        params={"status": "disabled"},
    )
    assert [item["id"] for item in disabled.json()["items"]] == [disabled_id]
    assert (
        client.get(
            "/api/v1/agents",
            headers=operator_headers(),
            params={"limit": 101},
        ).status_code
        == 422
    )


def test_operator_can_inspect_task_and_result_history(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    created = create_task(client, agent_id)
    claimed = claim_task(client, agent_id, credential)
    result = {
        "status": "completed",
        "task_id": claimed["id"],
        "agent_id": agent_id,
        "completed_at": NOW.isoformat(),
        "output": {"kind": "host.hostname", "hostname": "relay-host"},
    }
    client.post(
        f"/api/v1/agents/{agent_id}/results",
        headers=agent_headers(credential),
        json=result,
    )

    tasks = client.get(
        "/api/v1/tasks",
        headers=operator_headers(),
        params={"agent_id": agent_id, "status": "completed"},
    )
    assert tasks.status_code == 200
    assert tasks.json()["total"] == 1
    assert tasks.json()["items"][0]["id"] == created["id"]

    task = client.get(f"/api/v1/tasks/{created['id']}", headers=operator_headers())
    assert task.status_code == 200
    assert task.json()["status"] == "completed"

    stored = client.get(
        f"/api/v1/tasks/{created['id']}/result",
        headers=operator_headers(),
    )
    assert stored.status_code == 200
    stored_result = stored.json()["item"]["result"]
    assert stored_result == {
        **result,
        "completed_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert stored.json()["item"]["received_at"] == NOW.isoformat().replace("+00:00", "Z")

    missing_id = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/v1/tasks/{missing_id}", headers=operator_headers()).status_code == 404
    assert (
        client.get(
            f"/api/v1/tasks/{missing_id}/result",
            headers=operator_headers(),
        ).status_code
        == 404
    )


def test_operator_can_cancel_queued_task_idempotently(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    task = create_task(client, agent_id)
    route = f"/api/v1/tasks/{task['id']}/cancel"

    cancelled = client.post(route, headers=operator_headers())

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["lease_expires_at"] is None
    assert client.post(route, headers=operator_headers()).json() == cancelled.json()
    assert client.get(
        f"/api/v1/agents/{agent_id}/tasks/next",
        headers=agent_headers(credential),
    ).json() == {"task": None}


def test_task_cancellation_requires_operator_and_existing_task(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    task = create_task(client, agent_id)
    route = f"/api/v1/tasks/{task['id']}/cancel"

    assert client.post(route).status_code == 401
    assert client.post(route, headers=agent_headers(credential)).status_code == 401
    missing = client.post(
        "/api/v1/tasks/00000000-0000-0000-0000-000000000000/cancel",
        headers=operator_headers(),
    )
    assert missing.status_code == 404
    assert missing.json() == {"detail": "task not found"}


def test_cancelled_claim_rejects_results_and_finished_task_rejects_cancellation(
    client: TestClient,
) -> None:
    agent_id, credential = enroll(client)
    create_task(client, agent_id)
    claimed = claim_task(client, agent_id, credential)
    cancel_route = f"/api/v1/tasks/{claimed['id']}/cancel"
    assert client.post(cancel_route, headers=operator_headers()).status_code == 200

    result = {
        "status": "completed",
        "task_id": claimed["id"],
        "agent_id": agent_id,
        "completed_at": NOW.isoformat(),
        "output": {"kind": "host.hostname", "hostname": "relay-host"},
    }
    rejected = client.post(
        f"/api/v1/agents/{agent_id}/results",
        headers=agent_headers(credential),
        json=result,
    )
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": "task is not awaiting a result"}

    finished = create_task(client, agent_id)
    claim_task(client, agent_id, credential)
    result["task_id"] = finished["id"]
    assert (
        client.post(
            f"/api/v1/agents/{agent_id}/results",
            headers=agent_headers(credential),
            json=result,
        ).status_code
        == 200
    )
    conflict = client.post(
        f"/api/v1/tasks/{finished['id']}/cancel",
        headers=operator_headers(),
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "task cannot be cancelled from completed"}


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


def test_authenticated_agent_check_in_refreshes_metadata(
    client: TestClient,
    clock: MutableClock,
) -> None:
    agent_id, credential = enroll(client)
    route = f"/api/v1/agents/{agent_id}/check-ins"

    assert client.post(route, json=AGENT_METADATA).status_code == 401
    assert (
        client.post(route, headers=agent_headers("wrong"), json=AGENT_METADATA).status_code == 401
    )
    assert (
        client.post(
            route,
            headers={"Authorization": "Basic abc"},
            json=AGENT_METADATA,
        ).status_code
        == 401
    )

    clock.advance(timedelta(minutes=1))
    refreshed_metadata = {
        **AGENT_METADATA,
        "hostname": "renamed-host",
        "agent_version": "0.2.0",
        "architecture": None,
    }
    response = client.post(
        route,
        headers=agent_headers(credential),
        json=refreshed_metadata,
    )
    assert response.status_code == 200
    assert response.json() == {"agent_id": agent_id, "accepted": True}

    agents = client.get("/api/v1/agents", headers=operator_headers()).json()["items"]
    refreshed = next(agent for agent in agents if agent["id"] == agent_id)
    assert refreshed["metadata"] == refreshed_metadata
    assert refreshed["last_seen_at"] == clock.current.isoformat().replace("+00:00", "Z")

    malformed = client.post(
        route,
        headers=agent_headers(credential),
        json={**refreshed_metadata, "ip_address": "192.0.2.1"},
    )
    assert malformed.status_code == 422


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


def test_task_creation_is_idempotent_and_records_one_audit_event(client: TestClient) -> None:
    agent_id, _ = enroll(client)
    route = "/api/v1/tasks"
    headers = {**operator_headers(), "Idempotency-Key": "task-request-0001"}
    payload = {"agent_id": agent_id, "action": {"kind": "host.hostname"}}

    first = client.post(route, headers=headers, json=payload)
    repeated = client.post(route, headers=headers, json=payload)

    assert first.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json() == first.json()
    tasks = client.get(route, headers=operator_headers()).json()
    assert tasks["total"] == 1
    audit = client.get(
        "/api/v1/audit-events",
        headers=operator_headers(),
        params={"event_type": "task.created", "task_id": first.json()["id"]},
    ).json()
    assert audit["total"] == 1


def test_task_idempotency_key_rejects_conflicting_or_malformed_reuse(
    client: TestClient,
) -> None:
    agent_id, _ = enroll(client)
    route = "/api/v1/tasks"
    payload = {"agent_id": agent_id, "action": {"kind": "host.hostname"}}
    headers = {**operator_headers(), "Idempotency-Key": "task-request-0001"}
    assert client.post(route, headers=headers, json=payload).status_code == 201

    conflict = client.post(
        route,
        headers=headers,
        json={"agent_id": agent_id, "action": {"kind": "host.current_user"}},
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "idempotency key was already used for a different task"}
    for invalid_key in ("too-short", "task request with spaces"):
        response = client.post(
            route,
            headers={**operator_headers(), "Idempotency-Key": invalid_key},
            json=payload,
        )
        assert response.status_code == 422


def test_task_idempotency_insert_race_has_deterministic_http_outcomes(
    client: TestClient,
) -> None:
    agent_id, _ = enroll(client)
    route = "/api/v1/tasks"
    payload = {"agent_id": agent_id, "action": {"kind": "host.hostname"}}
    headers = {**operator_headers(), "Idempotency-Key": "task-request-0001"}
    first = client.post(route, headers=headers, json=payload)
    assert first.status_code == 201
    original_lookup = TaskRepository.get_by_idempotency_key
    lookup_count = 0

    def hide_initial_lookup(
        repository: TaskRepository,
        operator_id: OperatorId,
        key_digest: IdempotencyKeyDigest,
    ) -> Task | None:
        nonlocal lookup_count
        lookup_count += 1
        if lookup_count == 1:
            return None
        return original_lookup(repository, operator_id, key_digest)

    with patch.object(TaskRepository, "get_by_idempotency_key", new=hide_initial_lookup):
        repeated = client.post(route, headers=headers, json=payload)

    assert repeated.status_code == 201
    assert repeated.json() == first.json()

    lookup_count = 0
    with patch.object(TaskRepository, "get_by_idempotency_key", new=hide_initial_lookup):
        conflict = client.post(
            route,
            headers=headers,
            json={"agent_id": agent_id, "action": {"kind": "host.current_user"}},
        )

    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "idempotency key was already used for a different task"}


def test_idempotent_task_retry_survives_later_agent_disable(client: TestClient) -> None:
    agent_id, _ = enroll(client)
    route = "/api/v1/tasks"
    payload = {"agent_id": agent_id, "action": {"kind": "host.hostname"}}
    headers = {**operator_headers(), "Idempotency-Key": "task-request-0001"}
    first = client.post(route, headers=headers, json=payload)
    client.post(f"/api/v1/agents/{agent_id}/disable", headers=operator_headers())

    assert client.post(route, headers=headers, json=payload).json() == first.json()
    new_request = client.post(
        route,
        headers={**operator_headers(), "Idempotency-Key": "task-request-0002"},
        json=payload,
    )
    assert new_request.status_code == 409
    assert new_request.json() == {"detail": "agent is disabled"}


def test_task_creation_without_idempotency_key_remains_non_idempotent(
    client: TestClient,
) -> None:
    agent_id, _ = enroll(client)

    first = create_task(client, agent_id)
    second = create_task(client, agent_id)

    assert first["id"] != second["id"]


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
        json=AGENT_METADATA,
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


def test_disable_between_authentication_and_poll_prevents_claim(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    task = create_task(client, agent_id)
    lock_agent = AgentRepository.lock_for_update

    def disable_before_lock(
        repository: AgentRepository,
        polled_agent_id: AgentId,
        *,
        active_only: bool = False,
    ) -> bool:
        if not active_only:
            return lock_agent(repository, polled_agent_id, active_only=active_only)
        response = client.post(
            f"/api/v1/agents/{agent_id}/disable",
            headers=operator_headers(),
        )
        assert response.status_code == 200
        return lock_agent(repository, polled_agent_id, active_only=active_only)

    with patch.object(AgentRepository, "lock_for_update", new=disable_before_lock):
        response = client.get(
            f"/api/v1/agents/{agent_id}/tasks/next",
            headers=agent_headers(credential),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid credentials"}
    stored = client.get(f"/api/v1/tasks/{task['id']}", headers=operator_headers())
    assert stored.status_code == 200
    assert stored.json()["status"] == "queued"


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


def test_result_insert_race_has_deterministic_http_outcomes(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    create_task(client, agent_id)
    task = claim_task(client, agent_id, credential)
    payload = {
        "status": "completed",
        "task_id": task["id"],
        "agent_id": agent_id,
        "completed_at": NOW.isoformat(),
        "output": {"kind": "host.hostname", "hostname": "relay-host"},
    }
    route = f"/api/v1/agents/{agent_id}/results"
    original_add = ResultRepository.add

    def identical_race(
        repository: ResultRepository,
        result: ActionResult,
        *,
        received_at: datetime,
    ) -> bool:
        assert original_add(repository, result, received_at=received_at)
        return False

    with patch.object(ResultRepository, "add", new=identical_race):
        assert (
            client.post(route, headers=agent_headers(credential), json=payload).status_code == 200
        )

    def missing_winner(
        repository: ResultRepository,
        result: ActionResult,
        *,
        received_at: datetime,
    ) -> bool:
        del repository, result, received_at
        return False

    with patch.object(ResultRepository, "add", new=missing_winner):
        response = client.post(route, headers=agent_headers(credential), json=payload)

    assert response.status_code == 409
    assert response.json() == {"detail": "task already has a result"}


def test_result_state_race_has_deterministic_http_outcomes(client: TestClient) -> None:
    agent_id, credential = enroll(client)
    create_task(client, agent_id)
    task = claim_task(client, agent_id, credential)
    payload = {
        "status": "completed",
        "task_id": task["id"],
        "agent_id": agent_id,
        "completed_at": NOW.isoformat(),
        "output": {"kind": "host.hostname", "hostname": "relay-host"},
    }
    expected = ActionSuccess(
        task_id=TaskId(UUID(task["id"])),
        agent_id=AgentId(UUID(agent_id)),
        completed_at=NOW,
        output=HostnameOutput(hostname="relay-host"),
    )
    route = f"/api/v1/agents/{agent_id}/results"

    with (
        patch.object(TaskRepository, "finish_claimed", return_value=False),
        patch.object(ResultRepository, "get", side_effect=[None, expected]),
    ):
        identical = client.post(route, headers=agent_headers(credential), json=payload)

    assert identical.status_code == 200
    assert identical.json()["status"] == "completed"

    with (
        patch.object(TaskRepository, "finish_claimed", return_value=False),
        patch.object(ResultRepository, "get", side_effect=[None, None]),
    ):
        cancelled = client.post(route, headers=agent_headers(credential), json=payload)

    assert cancelled.status_code == 409
    assert cancelled.json() == {"detail": "task is no longer awaiting a result"}


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
    audit = client.get(
        "/api/v1/audit-events",
        headers=operator_headers(),
        params={"event_type": "task.failed"},
    ).json()
    assert audit["total"] == 1
    assert audit["items"][0]["task_id"] == task["id"]


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


@pytest.mark.parametrize(
    "completed_at",
    [
        NOW - timedelta(seconds=301),
        NOW + timedelta(seconds=301),
    ],
)
def test_result_completion_time_must_be_plausible(
    client: TestClient,
    completed_at: datetime,
) -> None:
    agent_id, credential = enroll(client)
    create_task(client, agent_id)
    task = claim_task(client, agent_id, credential)
    result = {
        "status": "completed",
        "task_id": task["id"],
        "agent_id": agent_id,
        "completed_at": completed_at.isoformat(),
        "output": {"kind": "host.hostname", "hostname": "relay-host"},
    }

    response = client.post(
        f"/api/v1/agents/{agent_id}/results",
        headers=agent_headers(credential),
        json=result,
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "result completion time is implausible"}


def test_request_size_is_bounded(client: TestClient) -> None:
    response = client.post(
        "/api/v1/agents/enroll",
        headers={**bootstrap_headers(), "Content-Length": str(65 * 1024)},
        content=b"{}",
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}


def test_chunked_request_size_is_measured_before_body_parsing(client: TestClient) -> None:
    def oversized_chunks() -> Iterator[bytes]:
        yield b"x" * (32 * 1024)
        yield b"x" * (32 * 1024 + 1)

    response = client.post(
        "/api/v1/agents/enroll",
        headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
        content=oversized_chunks(),
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}
