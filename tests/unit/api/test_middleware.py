import asyncio
from typing import cast
from unittest.mock import patch

import pytest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from c2_relay.api.middleware import (
    AuthenticationFailureLimitMiddleware,
    RequestSizeLimitMiddleware,
)


class MutableMonotonicClock:
    def __init__(self) -> None:
        self.current = 0.0

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


def http_scope(
    *,
    path: str = "/api/v1/tasks",
    client: tuple[str, int] | None = ("127.0.0.1", 1),
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Scope:
    return cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": headers or [],
            "client": client,
            "server": ("testserver", 80),
        },
    )


def invoke(middleware: ASGIApp, scope: Scope) -> list[Message]:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    async def call_middleware() -> None:
        await middleware(scope, receive, send)

    asyncio.run(call_middleware())
    return sent


def test_authentication_failure_limit_blocks_then_expires() -> None:
    clock = MutableMonotonicClock()
    downstream_calls = 0

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal downstream_calls
        del scope, receive
        downstream_calls += 1
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b'{"detail":"invalid credentials"}'})

    middleware = AuthenticationFailureLimitMiddleware(
        cast(ASGIApp, downstream),
        max_failures=2,
        window_seconds=60,
        clock=clock,
    )
    scope = http_scope(
        headers=[
            (b"authorization", b"Bearer never-log-this"),
            (b"x-forwarded-for", b"198.51.100.9"),
        ]
    )

    with patch("c2_relay.api.middleware.logger.warning") as warning:
        first = invoke(middleware, scope)
        second = invoke(middleware, scope)
        blocked = invoke(middleware, scope)
        blocked_again = invoke(middleware, scope)

    assert first[0]["status"] == 401
    assert second[0]["status"] == 401
    assert blocked[0]["status"] == 429
    assert blocked_again[0]["status"] == 429
    assert dict(blocked[0]["headers"])[b"retry-after"] == b"60"
    assert blocked[1]["body"] == b'{"detail":"too many authentication failures"}'
    assert downstream_calls == 2
    assert warning.call_count == 3
    assert sum("rate limit exceeded" in call.args[0] for call in warning.call_args_list) == 1
    rendered_calls = repr(warning.call_args_list)
    assert "never-log-this" not in rendered_calls
    assert "198.51.100.9" not in rendered_calls
    assert "127.0.0.1" in rendered_calls

    clock.advance(60)
    assert invoke(middleware, scope)[0]["status"] == 401
    assert downstream_calls == 3


def test_authentication_failure_limit_is_bounded_and_uses_direct_peers() -> None:
    downstream_calls = 0

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal downstream_calls
        del scope, receive
        downstream_calls += 1
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = AuthenticationFailureLimitMiddleware(
        cast(ASGIApp, downstream),
        max_failures=1,
        window_seconds=60,
        max_tracked_peers=1,
    )
    forwarded = [(b"x-forwarded-for", b"203.0.113.10")]
    first_peer = http_scope(client=("192.0.2.1", 1), headers=forwarded)
    second_peer = http_scope(client=("192.0.2.2", 1), headers=forwarded)

    assert invoke(middleware, first_peer)[0]["status"] == 401
    assert invoke(middleware, second_peer)[0]["status"] == 401
    assert invoke(middleware, first_peer)[0]["status"] == 401
    assert downstream_calls == 3


def test_authentication_failure_limit_handles_unknown_and_successful_peers() -> None:
    status_code = 401
    downstream_calls = 0

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal downstream_calls
        del scope, receive
        downstream_calls += 1
        await send({"type": "http.response.start", "status": status_code, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = AuthenticationFailureLimitMiddleware(
        cast(ASGIApp, downstream),
        max_failures=1,
        window_seconds=60,
    )
    unknown_peer = http_scope(client=None)

    assert invoke(middleware, unknown_peer)[0]["status"] == 401
    assert invoke(middleware, unknown_peer)[0]["status"] == 429
    status_code = 204
    known_peer = http_scope(client=("192.0.2.50", 1))
    assert invoke(middleware, known_peer)[0]["status"] == 204
    assert invoke(middleware, known_peer)[0]["status"] == 204
    assert downstream_calls == 3


def test_authentication_failure_limit_bypasses_health_and_non_http_scopes() -> None:
    downstream_calls = 0

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal downstream_calls
        del scope, receive, send
        downstream_calls += 1

    middleware = AuthenticationFailureLimitMiddleware(
        cast(ASGIApp, downstream),
        max_failures=1,
        window_seconds=60,
    )

    invoke(middleware, http_scope(path="/api/v1/health"))
    invoke(middleware, cast(Scope, {"type": "websocket"}))
    assert downstream_calls == 2


@pytest.mark.parametrize("declared_length", [b"2", b"invalid"])
def test_request_limit_counts_streamed_bytes(declared_length: bytes) -> None:
    sent: list[Message] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        del scope
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-length", declared_length)],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
        },
    )

    async def receive() -> Message:
        return {"type": "http.request", "body": b"x" * 11, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = RequestSizeLimitMiddleware(cast(ASGIApp, downstream), max_bytes=10)
    asyncio.run(middleware(scope, receive, send))

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert sent[1]["body"] == b'{"detail":"request body too large"}'


def test_request_limit_forwards_non_body_asgi_messages() -> None:
    sent: list[Message] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        del scope
        assert await receive() == {"type": "http.disconnect"}
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope = cast(Scope, {"type": "http", "headers": []})

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = RequestSizeLimitMiddleware(cast(ASGIApp, downstream), max_bytes=10)
    asyncio.run(middleware(scope, receive, send))

    assert sent[0]["status"] == 204
