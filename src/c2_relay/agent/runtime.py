"""Polling lifecycle for an enrolled agent."""

import random
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from threading import Event

import httpx2
from pydantic import SecretStr

from c2_relay.agent.actions import ActionRegistry
from c2_relay.agent.client import RelayClient
from c2_relay.agent.identity import AgentIdentity, IdentityStore
from c2_relay.agent.metadata import collect_metadata
from c2_relay.agent.results import PendingResultStore
from c2_relay.models import ActionResult, AgentMetadata


class AgentRuntime:
    def __init__(
        self,
        client: RelayClient,
        identity_store: IdentityStore,
        result_store: PendingResultStore,
        actions: ActionRegistry,
        *,
        bootstrap_token: SecretStr | None,
        poll_interval: float,
        max_backoff: float,
        metadata_factory: Callable[[], AgentMetadata] = collect_metadata,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._client = client
        self._identity_store = identity_store
        self._result_store = result_store
        self._actions = actions
        self._bootstrap_token = bootstrap_token
        self._poll_interval = poll_interval
        self._max_backoff = max_backoff
        self._metadata_factory = metadata_factory
        self._clock = clock
        self._jitter = jitter

    def run(self, stop: Event) -> None:
        failures = 0
        identity: AgentIdentity | None = None
        try:
            while not stop.is_set():
                try:
                    if identity is None:
                        identity = self._load_or_enroll()
                    self.run_once(identity)
                    failures = 0
                    delay = self._poll_interval
                except httpx2.HTTPStatusError as error:
                    if error.response.status_code in {
                        HTTPStatus.UNAUTHORIZED,
                        HTTPStatus.FORBIDDEN,
                    }:
                        return
                    failures += 1
                    delay = self._backoff_delay(failures)
                except httpx2.HTTPError:
                    failures += 1
                    delay = self._backoff_delay(failures)
                stop.wait(delay)
        finally:
            self._client.close()

    def _backoff_delay(self, failures: int) -> float:
        exponent = min(failures - 1, 16)
        base_delay = min(self._poll_interval * (2**exponent), self._max_backoff)
        return float(min(base_delay + base_delay * 0.2 * self._jitter(), self._max_backoff))

    def run_once(self, identity: AgentIdentity) -> None:
        self._deliver_pending(identity)
        self._client.check_in(
            identity.agent_id,
            identity.credential,
            self._metadata_factory(),
        )
        task = self._client.next_task(identity.agent_id, identity.credential)
        if task is None:
            return
        result = self._actions.execute(task, identity.agent_id, clock=self._clock)
        self._result_store.save(result)
        self._deliver_result(identity, result)

    def _deliver_pending(self, identity: AgentIdentity) -> None:
        result = self._result_store.load()
        if result is None:
            return
        self._deliver_result(identity, result)

    def _deliver_result(self, identity: AgentIdentity, result: ActionResult) -> None:
        if result.agent_id != identity.agent_id:
            raise RuntimeError("pending result belongs to a different agent identity")
        try:
            self._client.submit_result(identity.agent_id, identity.credential, result)
        except httpx2.HTTPStatusError as error:
            if error.response.status_code in {HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT}:
                self._result_store.clear()
                return
            raise
        self._result_store.clear()

    def _load_or_enroll(self) -> AgentIdentity:
        identity = self._identity_store.load()
        if identity is not None:
            return identity
        if self._bootstrap_token is None:
            msg = "agent is not enrolled and no bootstrap token is configured"
            raise RuntimeError(msg)
        enrollment = self._client.enroll(self._metadata_factory(), self._bootstrap_token)
        identity = AgentIdentity(
            agent_id=enrollment.agent_id,
            credential=SecretStr(enrollment.credential),
        )
        self._identity_store.save(identity)
        return identity
