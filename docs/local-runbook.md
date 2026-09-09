# Local verification runbook

This runbook verifies the assembled `0.2.0` workflow on an authorized local machine.

## Prepare

Create `.env` from `.env.example` and set `C2_RELAY_BOOTSTRAP_TOKEN` to a unique value containing
at least 32 characters. Keep the default localhost server and SQLite database settings.

Install dependencies and create the database schema:

```bash
uv sync
uv run alembic upgrade head
uv run c2-relay-operator create --name "Local Operator"
```

Store the displayed operator credential securely. It is shown only once and is required to
create tasks.

The default task claim lease is 30 seconds. It can be changed with
`C2_RELAY_TASK_LEASE_SECONDS`; keep it longer than the expected action and result-submission time.
Agent completion times allow five minutes of clock skew by default, configurable through
`C2_RELAY_RESULT_CLOCK_SKEW_SECONDS`.

Authentication throttling permits 10 failed attempts per direct peer in a rolling 60-second
window by default. Configure it with `C2_RELAY_AUTH_FAILURE_LIMIT` and
`C2_RELAY_AUTH_FAILURE_WINDOW_SECONDS`. A blocked request receives `429 Too Many Requests` and a
`Retry-After` header; restarting this single-process server clears the in-memory history.

## Start the server

In the first terminal:

```bash
uv run c2-relay-server
```

Confirm the health endpoint responds and review the generated API documentation:

```text
http://127.0.0.1:8000/api/v1/health
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/openapi.json
```

## Enroll and run the agent

In a second terminal:

```bash
uv run c2-relay-agent
```

After enrollment, confirm that `agent-state.json` exists. On Linux or macOS, its mode must be
owner-only:

```bash
stat -f '%Sp' agent-state.json 2>/dev/null || stat -c '%A' agent-state.json
```

Each authenticated check-in refreshes the hostname, operating system, username, agent version,
and architecture shown to operators. `last_seen_at` always comes from the server clock. These host
facts are self-reported inventory, not independently verified evidence.

If result submission is interrupted, the agent temporarily stores `pending-result.json` with the
same owner-only permissions. The file is removed after successful delivery or a permanent stale
result response.

In Swagger UI, select **Authorize** and enter the operator credential. Then use the agent
identifier displayed in `agent-state.json` to create one task for each allowlisted action:

```text
host.hostname
host.current_user
host.operating_system
```

For `POST /api/v1/tasks`, set the optional `Idempotency-Key` header to a fresh UUID or another
unique 16-to-128-character value. Retrying the same agent and action with the same key returns the
original task. Reusing that key with a different agent or action returns `409 Conflict`. The
server stores a SHA-256 digest of the key, not its plaintext value.

The running agent should claim and complete each task. Use the operator-authenticated agent and
task read endpoints in Swagger UI to inspect status and stored results. The list endpoints support
bounded `limit` and `offset` pagination plus status filters; tasks can also be filtered by agent.
Use `POST /api/v1/tasks/{task_id}/cancel` to withdraw a queued or claimed task. Repeating the
request is safe; completed, failed, or expired tasks return a conflict instead of being rewritten.

Use the operator-authenticated `GET /api/v1/audit-events` endpoint to inspect successful state
changes. It supports bounded pagination and filters for event type, operator, agent, and task IDs.
Routine check-ins with unchanged metadata do not add rows. Audit responses deliberately omit
credentials, host metadata, action output, and error details.

For a local persistence cross-check without printing credential digests:

```bash
sqlite3 c2-relay.db \
  "SELECT id, status, updated_at FROM tasks ORDER BY created_at;"
sqlite3 c2-relay.db \
  "SELECT task_id, status, completed_at FROM task_results ORDER BY completed_at;"
```

The operator-authenticated `POST /api/v1/agents/{agent_id}/disable` endpoint can revoke an agent.
Afterward, that agent's credential must receive `401` and task creation for it must receive `409`.

Stop the agent and server with `Ctrl-C`, restart both, and confirm the agent reuses its existing
identity rather than enrolling again.

## Release checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv lock --check
uv build
```

Expected artifacts are a wheel and source distribution under `dist/`. Build artifacts, local
configuration, the database, and agent identity are excluded from Git.
