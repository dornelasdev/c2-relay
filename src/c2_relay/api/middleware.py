"""ASGI middleware for bounded requests and authentication abuse controls."""

import logging
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from math import ceil
from threading import Lock
from time import monotonic

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _PeerFailures:
    attempts: deque[float] = field(default_factory=deque)
    throttle_reported: bool = False


class _AuthenticationFailureLimiter:
    def __init__(
        self,
        *,
        max_failures: int,
        window_seconds: float,
        max_tracked_peers: int,
        clock: Callable[[], float],
    ) -> None:
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._max_tracked_peers = max_tracked_peers
        self._clock = clock
        self._peers: OrderedDict[str, _PeerFailures] = OrderedDict()
        self._lock = Lock()

    def record_failure(self, peer: str) -> None:
        now = self._clock()
        with self._lock:
            failures = self._peers.get(peer)
            if failures is None:
                failures = _PeerFailures()
                self._peers[peer] = failures
            self._prune(failures, now)
            failures.attempts.append(now)
            self._peers.move_to_end(peer)
            if len(self._peers) > self._max_tracked_peers:
                self._peers.popitem(last=False)

    def retry_after(self, peer: str) -> tuple[int, bool] | None:
        now = self._clock()
        with self._lock:
            failures = self._peers.get(peer)
            if failures is None:
                return None
            self._prune(failures, now)
            if not failures.attempts:
                del self._peers[peer]
                return None
            self._peers.move_to_end(peer)
            if len(failures.attempts) < self._max_failures:
                return None
            retry_after = max(
                1,
                ceil(failures.attempts[0] + self._window_seconds - now),
            )
            first_report = not failures.throttle_reported
            failures.throttle_reported = True
            return retry_after, first_report

    def _prune(self, failures: _PeerFailures, now: float) -> None:
        cutoff = now - self._window_seconds
        while failures.attempts and failures.attempts[0] <= cutoff:
            failures.attempts.popleft()
        if not failures.attempts:
            failures.throttle_reported = False


class AuthenticationFailureLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_failures: int,
        window_seconds: float,
        max_tracked_peers: int = 10_000,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._app = app
        self._limiter = _AuthenticationFailureLimiter(
            max_failures=max_failures,
            window_seconds=window_seconds,
            max_tracked_peers=max_tracked_peers,
            clock=clock,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_protected_api_path(scope):
            await self._app(scope, receive, send)
            return

        peer = self._peer(scope)
        method = scope.get("method", "")
        path = scope.get("path", "")
        limited = self._limiter.retry_after(peer)
        if limited is not None:
            retry_after, first_report = limited
            if first_report:
                logger.warning(
                    "authentication rate limit exceeded method=%r path=%r peer=%r retry_after=%d",
                    method,
                    path,
                    peer,
                    retry_after,
                )
            response = JSONResponse(
                status_code=HTTPStatus.TOO_MANY_REQUESTS,
                content={"detail": "too many authentication failures"},
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        status_code: int | None = None

        async def observe_response(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self._app(scope, receive, observe_response)
        if status_code == HTTPStatus.UNAUTHORIZED:
            self._limiter.record_failure(peer)
            logger.warning(
                "authentication failed method=%r path=%r peer=%r",
                method,
                path,
                peer,
            )

    @staticmethod
    def _is_protected_api_path(scope: Scope) -> bool:
        path = str(scope.get("path", ""))
        return path.startswith("/api/v1/") and path != "/api/v1/health"

    @staticmethod
    def _peer(scope: Scope) -> str:
        client = scope.get("client")
        return "unknown" if client is None else str(client[0])


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        content_length = next(
            (value for name, value in scope["headers"] if name == b"content-length"),
            None,
        )
        if (
            content_length is not None
            and content_length.isdigit()
            and int(content_length) > self._max_bytes
        ):
            await self._reject(scope, receive, send)
            return

        body = bytearray()
        body_complete = False
        terminal_message: Message | None = None
        while not body_complete:
            message = await receive()
            if message["type"] != "http.request":
                terminal_message = message
                break
            body.extend(message.get("body", b""))
            if len(body) > self._max_bytes:
                await self._reject(scope, receive, send)
                return
            body_complete = not message.get("more_body", False)

        body_sent = False
        terminal_sent = False

        async def buffered_receive() -> Message:
            nonlocal body_sent, terminal_sent
            if not body_sent and (body or body_complete):
                body_sent = True
                return {
                    "type": "http.request",
                    "body": bytes(body),
                    "more_body": not body_complete,
                }
            if terminal_message is not None and not terminal_sent:
                terminal_sent = True
                return terminal_message
            return await receive()

        await self._app(scope, buffered_receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            content={"detail": "request body too large"},
        )
        await response(scope, receive, send)
