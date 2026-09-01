# Local verification runbook

This runbook verifies the assembled `0.1.0` workflow on an authorized local machine.

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

The running agent should claim and complete each task. Inspect persisted state without printing
the credential digest:

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
