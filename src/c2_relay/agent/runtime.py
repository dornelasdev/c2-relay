"""Polling lifecycle for an enrolled agent."""

import random
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event

import httpx2
from pydantic import SecretStr

from c2_relay.agent.actions import ActionRegistry
from c2_relay.agent.client import RelayClient
from c2_relay.agent.identity import AgentIdentity, IdentityStore
from c2_relay.agent.metadata import collect_metadata
from c2_relay.models import AgentMetadata


class AgentRuntime:
    def __init__(
        self,
        client: RelayClient,
        identity_store: IdentityStore,
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
        self._actions = actions
        self._bootstrap_token = bootstrap_token
        self._poll_interval = poll_interval
        self._max_backoff = max_backoff
        self._metadata_factory = metadata_factory
        self._clock = clock
        self._jitter = jitter

    def run(self, stop: Event) -> None:
        failures = 0
        try:
            identity = self._load_or_enroll()
            while not stop.is_set():
                try:
                    self.run_once(identity)
                    failures = 0
                    delay = self._poll_interval
                except httpx2.HTTPError:
                    failures += 1
                    exponent = min(failures - 1, 16)
                    base_delay = min(self._poll_interval * (2**exponent), self._max_backoff)
                    delay = min(base_delay + base_delay * 0.2 * self._jitter(), self._max_backoff)
                stop.wait(delay)
        finally:
            self._client.close()

    def run_once(self, identity: AgentIdentity) -> None:
        self._client.check_in(identity.agent_id, identity.credential)
        task = self._client.next_task(identity.agent_id, identity.credential)
        if task is None:
            return
        result = self._actions.execute(task, identity.agent_id, completed_at=self._clock())
        self._client.submit_result(identity.agent_id, identity.credential, result)

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
