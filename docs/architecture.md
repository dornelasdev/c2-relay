# Architecture

## Status

This document records the implemented architecture of c2-relay. It grows alongside reviewed
implementation slices; planned behavior is not presented as completed behavior.

## Package layout

c2-relay uses a `src` layout so tests and development commands import the installed package,
not an accidental package path from the repository root.

The package separates domain contracts, persistence, HTTP API, services, and the agent runtime
under `c2_relay`. Each boundary depends on domain contracts rather than transport-specific data.

## Domain contracts

Domain models are strict, immutable Pydantic models with unknown fields rejected. They have no
dependency on FastAPI, SQLAlchemy, or a wire transport.

- Agent metadata contains bounded self-reported host facts. Network addresses are deliberately
  excluded because a server must derive them from the connection rather than trust client input.
- Actions are a discriminated allowlist. Version `0.2.1` supports only hostname, current-user,
  and operating-system discovery contracts; arbitrary command payloads cannot be represented.
- Results distinguish structured success output from bounded, machine-readable failure details.
- Tasks follow an explicit lifecycle. All models require offset-aware timestamps and normalize
  them to UTC.

The allowed task transitions are:

```text
queued  -> claimed | expired | cancelled
claimed -> queued | running | expired | cancelled
running -> completed | failed | cancelled
```

Completed, failed, expired, and cancelled tasks are terminal. `expired` is reserved for a future
terminal-expiry policy and is not emitted by current application services. A claim instead carries
a bounded lease; elapsed claims return to the queue during the agent's next poll.

## Configuration

Runtime settings use the `C2_RELAY_` environment prefix and may be loaded from a local `.env`
file. Development defaults use a local SQLite database; `.env` files and database files are
excluded from version control.

Alembic and the API both resolve `Settings.database_url`, so migration commands and the running
server target the same configured database. Programmatic migration tests use an explicit Alembic
attribute override to remain isolated from developer configuration.

## Persistence

SQLAlchemy provides synchronous relational mappings for agents, tasks, and task results. The
application owns transactions through an explicit unit of work; repositories never commit
implicitly. Alembic migrations are the only supported mechanism for creating or evolving an
operational database.

Append-only audit rows record successful operational state changes in the same transaction as
the change they describe. Historical identifiers are stored without cascading foreign keys so a
future entity deletion cannot erase the corresponding audit trail. The application exposes no
audit update or delete operation.

Task polling uses a conditional `UPDATE ... RETURNING` claim rather than row-lock syntax that
SQLite ignores. Concurrent pollers therefore claim different queued tasks. Expired claims are
requeued in the same transaction before the next task is selected.

Optional task-creation idempotency keys are scoped to the authenticated operator. Only each key's
SHA-256 digest is persisted, and a database unique constraint makes concurrent identical requests
resolve to one task. Reusing a key for different agent or action data returns a conflict.

SQLite stores timestamps without timezone information, so a custom column type converts aware
timestamps to naive UTC at the storage boundary and restores aware UTC values on reads. Foreign
key enforcement is enabled for every SQLite connection.

Agent credential material is never accepted by persistence repositories. They accept only a
fixed-length SHA-256 digest produced from a high-entropy token. Credential generation and HTTP
authentication are owned by the API layer.

## HTTP API

FastAPI exposes versioned routes under `/api/v1`. The transport layer validates requests and
delegates persistence to a unit of work; it does not redefine domain lifecycle rules.

- Health is public and carries no operational data.
- Enrollment requires the configured bootstrap token and returns `503` when no token is
  configured. Task creation requires an active operator's bearer credential.
- Enrollment issues a high-entropy agent credential once and persists only its SHA-256 digest.
- Operator provisioning also displays its credential once and persists only the secret digest.
- Task creation accepts an optional 16-to-128-character `Idempotency-Key`. Repeating the same
  operator, key, agent, and action returns the original task; conflicting reuse returns `409`.
- Authenticated operators can list and filter agents and tasks, inspect individual tasks, and
  retrieve stored results through bounded read endpoints. Responses never expose credential
  material or its digest.
- Operators can inspect bounded audit history filtered by event type or related operator, agent,
  and task identifiers. Events contain no credentials, digests, host metadata, result payloads,
  or other free-form content.
- Operators can atomically cancel queued or claimed tasks. Cancellation is idempotent, cannot
  overwrite a terminal result, and prevents a concurrent result from overwriting cancellation.
- Check-in, task polling, and result submission require the enrolled agent's bearer credential.
- Each authenticated check-in refreshes the agent's bounded self-reported metadata and a
  server-generated `last_seen_at`. Authentication identifies the credential holder but does not
  attest that reported host facts are truthful.
- An authenticated operator can disable an agent. Disabled agents cannot authenticate or receive
  newly queued tasks, and repeating the disable operation is safe.
- Polling atomically claims queued work with a configurable lease. Late results are rejected and
  expired claims are recovered on the next poll. Results must match the authenticated agent,
  claimed task, and requested action. Repeating an identical result is idempotent; conflicting
  results fail.
- Agent-reported completion times must fall within configurable clock skew around the claim and
  server receipt times. The server stores its own `received_at` timestamp separately.
- Request bodies above 64 KiB are rejected while their ASGI byte stream is consumed, even if
  `Content-Length` is absent, malformed, or dishonest.
- Repeated `401` responses from one direct peer are tracked in a bounded, process-local sliding
  window. Once the configured threshold is reached, later protected API requests receive `429`
  with `Retry-After`; health remains available. Failures and the first throttle event are logged
  with method, path, and direct peer, but never credentials, bodies, or credential digests.

The development server binds to localhost by default. TLS is an external deployment requirement;
c2-relay does not claim secure network transport when served directly over plain HTTP.

## Agent runtime

The agent maintains one long-lived HTTPX2 client with bounded request timeouts. It enrolls once,
stores the issued identity through an atomic owner-only file, checks in, polls for one task, and
submits a structured result. Before submission, the result is written to an owner-only local
outbox. A restarted agent delivers pending work before checking in or polling again. Identical
submissions are safe to retry at the server boundary; stale `404` or `409` results are discarded
so lease recovery can proceed.

The action registry maps the three domain action kinds directly to Python standard-library host
inspection functions. There is no subprocess, shell, dynamic import, or arbitrary command path.
Initial enrollment and later transient HTTP failures use capped exponential backoff with jitter.
Authentication rejection (`401` or `403`) is permanent and stops the runtime instead of retrying
a revoked credential. Signal-driven shutdown uses an interruptible event wait rather than an
uninterruptible sleep.

Lease recovery provides at-least-once execution. This is acceptable for the current read-only
actions; mutating actions would additionally require execution-level replay protection because
task-creation idempotency does not prevent an agent from rerunning a recovered claim.
Cancelling a claimed task withdraws it at the server boundary but cannot interrupt an action that
has already started on the agent host.

Identity and outbox files share an atomic JSON writer that uses uniquely created temporary files,
atomic replacement, and `0600` permissions. This avoids predictable temporary-file names and
prevents partial JSON from becoming active state.

## Supported environments

The project supports CPython 3.12 through 3.14. Dependencies are declared in `pyproject.toml`
and resolved reproducibly in `uv.lock`.

## Security posture

c2-relay is intended for authorized environments. Secure defaults, bounded behavior, explicit
authentication, strict input validation, and auditable state transitions are architectural
requirements rather than optional extensions.

Audit history is operational accountability, not cryptographic tamper evidence. A database
administrator can modify SQLite directly. Failed authentication is security-log telemetry rather
than an audit row because an unauthenticated caller cannot supply a trusted operator or agent
identity.

Authentication throttling deliberately ignores `X-Forwarded-For`; trusting that header without a
configured proxy would make the limiter spoofable. Its peer state resets when the process restarts
and is not shared by multiple workers. A production deployment therefore still needs a trusted
edge or shared-store limiter, with an explicit forwarded-client policy.
