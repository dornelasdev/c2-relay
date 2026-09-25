# Changelog

All notable changes to c2-relay are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Requeue pre-lease `claimed` tasks during database upgrade so existing v0.1 tasks remain
  readable and recoverable after migrating to v0.2.
- Serialize agent disabling with task polling so a revoked agent cannot claim a task after
  its credentials have been checked.

## [0.2.0] - 2026-09-10

### Added

- Added persistent operator identities, one-time `c2o.` credentials, and the
  `c2-relay-operator` provisioning command.
- Added active and disabled agent lifecycle states, authenticated metadata refresh, and an
  idempotent operator endpoint for disabling agents.
- Added bounded operator endpoints for listing agents and tasks, inspecting individual tasks
  and results, cancelling work, and filtering audit history.
- Added configurable task leases, automatic expired-claim recovery, and late-result rejection.
- Added operator-scoped task-creation idempotency. Only the SHA-256 digest of each key is
  persisted, and conflicting reuse returns `409 Conflict`.
- Added append-only audit events for successful operator and agent state transitions.
- Added an independent server-side `received_at` timestamp and configurable validation of
  agent-reported completion times.
- Added an owner-only pending-result outbox. The agent delivers saved results before resuming
  check-ins and polling after a restart.
- Added configuration for task leases, result clock skew, authentication throttling, result
  storage, request timeouts, polling, and capped retry backoff.

### Changed

- Restricted the bootstrap token to agent enrollment. Operator credentials now authorize agent
  inventory, task management, result inspection, and audit-history access.
- Changed task polling to use an atomic conditional claim compatible with SQLite concurrency.
- Changed cancellation into an idempotent state transition for queued and claimed tasks, with
  protection against concurrent result submission.
- Changed agent startup and transient transport handling to use capped exponential backoff with
  jitter. Authentication rejection stops the runtime instead of retrying a revoked credential.
- Changed identity and pending-result persistence to use a shared atomic JSON writer with unique
  temporary files and replacement semantics.
- Extended the database from migration `20260711_0001` through `20260902_0007` for operators,
  agent lifecycle state, task leases, result receipt times, audit events, and task idempotency.
- Updated Alembic and the API to resolve the same configured database URL.

### Fixed

- Corrected streamed request-size enforcement so oversized bodies return
  `413 Content Too Large` before FastAPI route parsing instead of becoming a generic
  `400 Bad Request`.

### Security

- Enforced the 64 KiB request-body limit against the consumed ASGI stream when
  `Content-Length` is absent, malformed, or dishonest.
- Added bounded, process-local throttling for repeated authentication failures from one direct
  peer, including `429 Too Many Requests` and `Retry-After` responses.
- Added security logging for authentication failures and initial throttle events without
  recording credentials, request bodies, or credential digests.
- Prevented operator APIs and audit events from exposing credentials, digests, result payloads,
  or other unbounded free-form content.
- Preserved owner-only `0600` permissions for agent identity and pending-result files on
  supported POSIX platforms.

### Known Limitations

- The development server uses localhost HTTP. TLS termination and secure network deployment are
  external responsibilities.
- SQLite is the development datastore, and audit history is not cryptographic tamper evidence
  against a database administrator.
- Authentication throttling is process-local, resets on restart, is not shared across workers,
  and deliberately ignores forwarded-client headers without a trusted proxy policy.
- Agent metadata is authenticated self-reporting, not host attestation; the server cannot prove
  that reported host facts are truthful.
- Lease recovery provides at-least-once execution. Future mutating actions will require
  execution-level replay protection.
- Cancelling a claimed task prevents server-side completion but cannot interrupt an action that
  has already started on the agent host.
- Secure local credential-file handling currently depends on POSIX permissions. Windows is not
  supported until equivalent ACL or operating-system credential-store protection is available.

## [0.1.0] - 2026-07-11

### Added

- Added strict, immutable Pydantic contracts for agent metadata, tasks, allowlisted actions, and
  structured results.
- Added a FastAPI service with versioned health, enrollment, check-in, task-polling, and result
  endpoints.
- Added SQLAlchemy persistence, SQLite foreign-key enforcement, an explicit unit of work, and
  the initial Alembic migration.
- Added high-entropy agent credentials with SHA-256 digest storage and bootstrap-authorized
  enrollment.
- Added an agent runtime with persistent owner-only identity storage, bounded HTTP timeouts,
  retry backoff, and signal-driven shutdown.
- Added three read-only host-information actions: hostname, current user, and operating system.
  The runtime does not provide shell or arbitrary-command execution.
- Added reproducible `uv` dependency management, strict Ruff and mypy checks, pytest coverage
  enforcement, packaging, CI, architecture documentation, and a local runbook.

[Unreleased]: https://github.com/dornelasdev/c2-relay/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/dornelasdev/c2-relay/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dornelasdev/c2-relay/releases/tag/v0.1.0
