# Local verification runbook

This runbook verifies the current development workflow on an authorized local machine.

## Prepare

Create `.env` from `.env.example`. Keep the default localhost server and SQLite database
settings. Leave legacy bootstrap enrollment disabled; new agents use one-time enrollment grants.

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

## Enrollment grants from the operator CLI

With the server running, issue a grant from the request terminal:

```bash
uv run c2-relay-operator grants issue
```

The CLI prompts for the operator's `c2o.` credential with hidden input. Its JSON output contains
the non-secret `grant` record and the one-time `token`. Treat that output as secret: it is the
same enrollment token returned by Swagger. Copy the token into the agent's
`C2_RELAY_AGENT_ENROLLMENT_TOKEN` setting before starting a new identity.

The default grant lifetime is 900 seconds. To request a different lifetime within the supported
60-to-86400-second range, or to revoke a grant that has not been redeemed:

```bash
uv run c2-relay-operator grants issue --expires-in 300
uv run c2-relay-operator grants revoke <grant_id>
```

Replace `<grant_id>` with the UUID in `grant.id`, not the full token. Revocation returns the
updated grant record without a token. Repeating revocation is safe; revoking a redeemed grant
returns HTTP `409`, while a missing grant returns HTTP `404`.

Grant commands call the running API; only `c2-relay-operator create` provisions an operator
directly through the local database. The CLI does not automatically retry grant issuance,
because a lost response may still correspond to a committed grant.

`C2_RELAY_OPERATOR_SERVER_URL` selects the API origin, and `C2_RELAY_OPERATOR_REQUEST_TIMEOUT`
sets the timeout in seconds. Each grant command accepts `--server-url` to override that origin.
Use an HTTP(S) origin without embedded credentials, a path prefix, query parameters, or a
fragment. Use certificate-validated HTTPS for communication across machines; the CLI does not
follow redirects or disable certificate verification.

For repeated commands, an optional `C2_RELAY_OPERATOR_CREDENTIAL` setting avoids the prompt.
Keep it in the operator's local environment or ignored local configuration; never include it
in an agent deployment's configuration. Leave it blank to use hidden interactive input.
When hidden input is unavailable, the CLI exits rather than falling back to visible input.
API errors produce a nonzero exit status and an HTTP status code without printing response
bodies or credentials.

## Enroll and run the agent

For a new identity, open Swagger UI's **Authorize** dialog, enter the operator credential in
**OperatorBearer**, and call
`POST /api/v1/enrollment-grants` with `{}` for the default 15-minute lifetime. Save the returned
`token` as `C2_RELAY_AGENT_ENROLLMENT_TOKEN` in `.env`, then start the agent before it expires.
The CLI commands above are an alternative to these Swagger requests; issuing through both
interfaces creates separate grants. The `c2g.` token is shown once and can enroll only one agent.
The operator can revoke an unused
grant with `POST /api/v1/enrollment-grants/{grant_id}/revoke`.

An existing `agent-state.json` is reused without a grant. Do not remove a working identity just
to repeat enrollment. Once the new agent has saved its identity, clear the consumed enrollment
token from `.env`.

In a second terminal:

```bash
uv run c2-relay-agent
```

After enrollment, confirm that `agent-state.json` exists. On Linux or macOS, its mode must be
owner-only:

```bash
stat -f '%Sp' agent-state.json 2>/dev/null || stat -c '%A' agent-state.json
```

Use the operator-authenticated audit endpoint to confirm one `enrollment_grant.redeemed` event
for the grant and one `agent.enrolled` event for the new agent. Invalid, expired, revoked, or
already consumed grant tokens receive `401`; they do not create additional agents. If the
enrollment response is lost or identity saving fails after server commit, disable the orphaned
agent and issue a new grant rather than retrying the consumed token.

For temporary compatibility with old shared-token enrollment, both server and agent must use
`C2_RELAY_ALLOW_LEGACY_BOOTSTRAP_ENROLLMENT=true` and a high-entropy `C2_RELAY_BOOTSTRAP_TOKEN`
containing at least 32 characters. Leave the agent grant token blank when using that flow.
Prefer a separate one-time grant for each new agent.

Each authenticated check-in refreshes the hostname, operating system, username, agent version,
and architecture shown to operators. `last_seen_at` always comes from the server clock. These host
facts are self-reported inventory, not independently verified evidence.

If result submission is interrupted, the agent temporarily stores `pending-result.json` with the
same owner-only permissions. The file is removed after successful delivery or a permanent stale
result response.
On startup, the agent rejects either existing state file if it is a symlink, is not a regular file,
is owned by another user, or has a mode other than `0600`.

Swagger UI has separate **OperatorBearer** and **AgentBearer** authorization fields. The operator
field accepts the `c2o.` credential; the agent field accepts the credential from `agent-state.json`.
Both can be authorized at the same time, and each route uses only its declared scheme. Agent
authorization is useful for manual agent-request PoCs; normal operator work needs only the
operator field. Enrollment uses its own token header rather than either bearer scheme.
The generated curl examples contain plaintext credentials, so redact them before sharing.

Use **OperatorBearer** for agent inventory, task management, and audit history. Then use the agent
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
