# C2-Relay v0.2.0 Verification Walkthrough

This writeup documents an end-to-end verification of C2-Relay v0.2.0 in an authorized local environment. It covers project setup, operator and agent identities, allowlisted task execution, audit history, and the reliability and security controls introduced in this release.

C2-Relay is a security-focused Python framework for authenticated agent coordination, task management, and operational telemetry.

Its architecture explores communication patterns associated with command-and-control systems, while v0.2.0 deliberately supports only three read-only host-information actions and does not invoke a shell.

> [!IMPORTANT]
> C2-Relay is under active development and is not ready for operational use. Use it only on systems you own or are explicitly authorized to test.

## Automated Verification

Before beginning the manual walkthrough, I synchronized the environment and ran the complete automated verification suite.

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv lock --check
```

These checks cover dependency synchronization, linting, formatting, static type analysis, automated tests, and lockfile consistency.

### Observed Results

- Ruff lint: All checks passed!
- Ruff formatting: 86 files already formatted
- mypy: Success: no issues found in 79 source files
- pytest: 100% total coverage, 232 passed
- Lockfile check: Resolved 37 packages

> File counts, dependency counts, and execution times may vary by platform or later repository changes. The required result for every command is a zero exit status with no reported violations.

## Local Environment Preparation

The old `agent-state.json` and `c2-relay.db` files used with v0.1.0 were archived before testing so I could examine the project from a clean state. I stopped both the agent and server processes before archiving the runtime state.

> The identity file and database were archived together because the stored agent credential corresponds to an agent record in that database.

With `uv run python -c "import c2_relay; print(c2_relay.__version__)"`, I confirmed that the current version was `0.2.0`.

I initialized the fresh database and verified its migration revision:

```bash
uv run alembic upgrade head
uv run alembic current
```

`alembic current` reported the expected applied revision: `20260902_0007 (head)`.

I configured a bootstrap token before starting the server and enrolling the new agent, and generated a high-entropy token with:

```bash
openssl rand -hex 32
```

I placed the value only in the ignored `.env` file as `C2_RELAY_BOOTSTRAP_TOKEN=<generated-value>`.

The bootstrap token authorizes only agent enrollment; it does not authorize operator and task-management requests.

## Operator Provisioning

I then needed an operator. To create one, I used:

```bash
uv run c2-relay-operator create --name "v0.2 Test Operator"
```

The command returned an operator UUID and a one-time credential beginning with `c2o.`. The UUID identifies the operator, while the credential is a secret used to authenticate operator requests.

This demonstrated a major change from v0.1.0: operator identity and task reliability became explicit parts of C2-Relay's early security model.

## Runtime Startup

### Terminal Layout

| Terminal | Purpose                                           | Main command                 |
| -------- | ------------------------------------------------- | ---------------------------- |
| `server` | Runs the FastAPI service and displays server logs | `uv run c2-relay-server`     |
| `agent`  | Runs or pauses the enrolled agent                 | `uv run c2-relay-agent`      |
| `input`  | Sends curl commands and performs local checks     | Varies by PoC                |
| Browser  | Uses Swagger UI for authenticated API requests    | `http://127.0.0.1:8000/docs` |

### Server Startup and Health Check

In the `server` pane, I started the server with `uv run c2-relay-server`.

A successful startup displayed messages similar to `Application startup complete` and `Uvicorn running on 127.0.0.1:8000`.

`curl -i http://127.0.0.1:8000/api/v1/health` returned `200 OK`, while the server access log recorded the same status with the client's ephemeral source port. This verified basic API availability, not database health or authentication.

### Agent Enrollment

In the `agent` pane, I ran `uv run c2-relay-agent`. The server received and logged the enrollment request, followed by check-in and task-polling requests.

The server received `POST /api/v1/agents/enroll` with `201 Created`, followed by `POST /api/v1/agents/{agent_id}/check-ins` and `GET /api/v1/agents/{agent_id}/tasks/next` with `200 OK`.

I performed a quick verification of the local identity file:

```bash
stat -f '%Sp' agent-state.json 2>/dev/null || stat -c '%A' agent-state.json
```

On macOS and Linux, the command returned `-rw-------`.

`agent-state.json` was created during agent enrollment and stores the *agent* UUID and credential for reuse across restarts.

It has owner-only permissions that protect that credential locally.

I did not include its contents in this writeup; I referred to its values only by their field names.

## Normal Task Workflow

```mermaid
sequenceDiagram
    actor Operator
    participant API as C2-Relay API
    participant Agent

    Note over Operator,API: Operator-authenticated management request
    Operator->>API: POST /api/v1/tasks
    API-->>Operator: 201 Created and task ID

    Note over API,Agent: Agent-authenticated runtime requests
    Agent->>API: GET /api/v1/agents/{agent_id}/tasks/next
    API-->>Agent: Claimed task and lease
    Agent->>Agent: Execute allowlisted action
    Agent->>API: POST /api/v1/agents/{agent_id}/results
    API-->>Agent: 200 OK

    Operator->>API: GET /api/v1/tasks/{task_id}
    API-->>Operator: Completed task state
    Operator->>API: GET /api/v1/tasks/{task_id}/result
    API-->>Operator: Stored structured result
    Operator->>API: GET /api/v1/audit-events
    API-->>Operator: Task lifecycle events
```

### Operator Authentication and Agent Inventory

On `http://127.0.0.1:8000/docs`, I accessed Swagger UI and selected "Authorize," which opened the HTTP Bearer authorization dialog.

> I first placed the random value "123" in the "Value" field. Although Swagger displayed "Authorized," this only confirmed that the browser stored the value; it did not authenticate against the API. When I made a request to `GET /api/v1/agents`, the API returned `401 Unauthorized` with `{"detail":"invalid credentials"}`.

I then replaced the invalid value with the *operator* credential and returned to `GET /api/v1/agents`. I expanded the section, selected "Try it out," and then "Execute." Swagger UI and the `server` log both showed `200 OK`, while the response body displayed the enrolled agent inventory.

The response included relevant information right away. `status` and `disabled_at` are new additions in v0.2.0, while `last_seen_at` already existed. Version 0.2.0 also refreshes self-reported agent metadata during check-ins.

It is important to remember that Swagger's generated curl command shows the full operator bearer credential even though the authorization dialog masks it. The credential is a secret, so I do not include it in this writeup.

With this, I confirmed operator authentication and agent-inventory access. The authenticated response contained the agent UUID and metadata, but no operator credential, agent credential, or credential digest.

### Task Creation and Completion

I created a normal task through `POST /api/v1/tasks` while Swagger remained authenticated with the *operator* credential. After selecting "Try it out," I supplied an optional `Idempotency-Key`. This 16-128-character token represented one intended task and allowed the same creation request to be retried without queuing duplicate work.

> For this local PoC, I used a simple and descriptive idempotency-key value for convenience.

In the `agent_id` field, I replaced Swagger's placeholder string with the enrolled agent UUID and selected the allowlisted `host.hostname` action. The request remained operator-authenticated; the field contained the agent UUID, not its credential.

The API returned `201 Created`, and the `server` log recorded `POST /api/v1/tasks HTTP/1.1`. The response provided the new task ID, which I kept for the following verification steps.

The running agent then called `GET /api/v1/agents/{agent_id}/tasks/next`, received `200 OK`, and claimed the queued task. It executed the `host.hostname` action and submitted its structured result through `POST /api/v1/agents/{agent_id}/results`, which also returned `200 OK`.

After the result was submitted, I called `GET /api/v1/tasks/{task_id}` using the task ID returned during creation. The endpoint returned `200 OK` with the task status set to `completed`, matching task and agent IDs, and `lease_expires_at: null`.

### Result Inspection

Using the same task ID, I called `GET /api/v1/tasks/{task_id}/result` and provided the value in the `task_id` path field. The endpoint returned `200 OK`, and the `server` log recorded `GET /api/v1/tasks/MY_TASK_ID/result HTTP/1.1` with the same status.

The stored result contained the matching task and agent IDs, the completed `host.hostname` output, and two relevant timestamps. `completed_at` was reported by the agent, while `received_at` was recorded independently by the server as a new reliability and telemetry field in v0.2.0.

### Audit History

To finish the normal task inspection, I called `GET /api/v1/audit-events` and filtered the response with the same `task_id`. The endpoint returned `200 OK` and contained the `task.completed`, `task.claimed`, and `task.created` events.

The `task.created` event contained the operator ID provisioned at the beginning because an *operator*-authenticated request initiated the task. The `task.claimed` and `task.completed` events had `operator_id: null` because they originated from *agent*-authenticated requests. All three events contained the same agent and task UUIDs, preserving the task's audit trail across its lifecycle.

### Task Cancellation

With the server still running, I stopped the agent in the `agent` pane so the next task would remain queued. While still authenticated as the *operator*, I created another task through `POST /api/v1/tasks` and used the descriptive idempotency key `normal-flow-cancel-0001`.

The API returned `201 Created`, and I kept the new task ID. I then called `POST /api/v1/tasks/{task_id}/cancel` with that ID. The endpoint returned `200 OK`, while Swagger showed `status: cancelled` and `lease_expires_at: null`.

I restarted the agent and observed its normal check-in and task-polling requests return `200 OK`. The agent did not claim or execute the cancelled task. I confirmed its final state through `GET /api/v1/tasks/{task_id}`, which returned `status: cancelled`.

Finally, I called `GET /api/v1/audit-events` and filtered by the cancelled task ID. The response contained one `task.created` event and one `task.cancelled` event. Both events contained matching operator, task, and agent IDs because the same authenticated operator created and cancelled the task.


## Security and Reliability PoCs

### Streamed Request-Size Enforcement

I tested how the server handled a streamed request body larger than 64 KiB. The configured threshold was 65,536 bytes, and the following command sent 65,537 bytes, exactly one byte over the limit:

> During the first release-verification attempt, the initial middleware raised an exception while FastAPI was parsing the request stream. FastAPI translated that exception into a generic `400 Bad Request` response instead of the intended size-limit response.

> I corrected the middleware to read the stream through a bounded buffer before route parsing and return `413 Content Too Large` directly when the accumulated body exceeds the limit.

```bash
head -c 65537 /dev/zero \
    | tr '\0' 'x' \
    | curl --http1.1 -i \
        -X POST http://127.0.0.1:8000/api/v1/agents/enroll \
        -H 'Content-Type: application/json' \
        -H 'Transfer-Encoding: chunked' \
        --data-binary @-
```

Because the request used chunked transfer encoding, the middleware could not rely on a declared `Content-Length` value and instead measured the actual bytes received from the body stream.

The command did not include a bootstrap credential or a valid enrollment payload because the request-size middleware rejected the body before authentication and route-level parsing.

After the correction, curl returned `413 Content Too Large` with the following response body, and the `server` log recorded the same status:

```json
{"detail":"request body too large"}
```

### Task Creation Idempotency

In the `agent` pane, I stopped the real agent with `Ctrl-C` and left the server running so newly created tasks would remain queued. Swagger remained authenticated with the *operator* credential.

On `POST /api/v1/tasks`, I supplied the idempotency key `poc-idempotency-0001`, the enrolled agent UUID, and the `host.hostname` action. I submitted the same request twice in sequence. Both requests returned `201 Created`, both responses contained the same task UUID, and both referenced the same queued task.

I then kept the same key but changed the action from `host.hostname` to `host.current_user`. The API returned `409 Conflict` with `{"detail":"idempotency key was already used for a different task"}`, proving that the key could not be reused for a different task payload.

Through `GET /api/v1/audit-events`, I filtered by the original task ID. The response returned `200 OK` with `total: 1` and contained one `task.created` event.

> Each request constructs a temporary candidate in memory. An identical retry finds the persistent original and discards its candidate, so only one API resource, database row, and creation audit event exists. Concurrent behavior is covered separately by the automated tests.

Finally, I returned to `POST /api/v1/tasks` and submitted the valid `host.hostname` payload twice without an `Idempotency-Key`. Both requests returned `201 Created` with different task IDs. `GET /api/v1/tasks` showed both independent tasks in the queued state.

### Idempotent Task Cancellation

Using one of the queued tasks from the preceding test, I called `POST /api/v1/tasks/{task_id}/cancel`. The endpoint returned `200 OK` with `status: cancelled`.

I called the same cancellation endpoint twice more with the same task ID. Each request returned `200 OK` with the same cancelled task state. The agent remained enrolled, but its runtime process was stopped, so it could not claim the task during the test.

Through `GET /api/v1/audit-events`, I filtered by the task ID and received `total: 2`: one `task.created` event and one `task.cancelled` event. The repeated cancellation requests did not create additional audit events, demonstrating that cancellation is idempotent.

> To restore a clean runtime state before the next PoC, I retrieved the remaining queued tasks through `GET /api/v1/tasks` and cancelled each one through `POST /api/v1/tasks/{task_id}/cancel`.

### Expired Claim Recovery

> I reran this PoC after correcting the sequence used during my first attempt. For that reason, the idempotency key retains `rerun` in its name.

With the agent runtime stopped and the server still running, I created a `host.hostname` task through `POST /api/v1/tasks`. I supplied the enrolled agent UUID and used `poc-lease-recovery-rerun-0001` as the idempotency key.

Swagger was still authenticated with the operator credential, so I replaced it with the agent credential. I then called `GET /api/v1/agents/{agent_id}/tasks/next` with the agent UUID. The endpoint returned `200 OK` with the newly created task in the `claimed` state and a timestamp in `lease_expires_at`.

After the first lease expired, I called the same endpoint again. It returned `200 OK` with the same task ID, `status: claimed`, and a new `lease_expires_at` timestamp. This demonstrated that the server automatically recovered the expired claim and allowed the agent to reclaim the task.

I then let the renewed lease expire without polling again. Before submitting a result through `POST /api/v1/agents/{agent_id}/results`, I generated the required current UTC timestamp with `date -u +"%Y-%m-%dT%H:%M:%SZ"`.

```json
{
  "status": "completed",
  "task_id": "<task-id>",
  "agent_id": "<agent-id>",
  "completed_at": "<current-UTC-timestamp>",
  "output": {
    "kind": "host.hostname",
    "hostname": "host-name"
  }
}
```

> Swagger initially provides `"string"` as a placeholder for the hostname field. I replaced it with a descriptive test value.

The result submission returned `409 Conflict` with `{"detail":"task lease has expired"}`. The server therefore rejected a result submitted after the renewed claim had expired.

As a separate validation test, I removed the trailing `Z` from `completed_at`, making the timestamp timezone-naive. The API returned `422 Unprocessable Content` because Pydantic rejected the invalid timestamp before the request reached the task-result business logic.

To restore a clean runtime state, I switched Swagger back to the operator credential and cancelled the task through `POST /api/v1/tasks/{task_id}/cancel`.

After the cleanup, I filtered `GET /api/v1/audit-events` by the task ID. The response contained one `task.created` event, two `task.claimed` events, and one `task.cancelled` event. The rejected `422` and `409` result submissions did not generate audit events.

### Metadata Trust Boundary

With the agent runtime still stopped and the server running, I authenticated through Swagger using the *agent* credential. I then called `POST /api/v1/agents/{agent_id}/check-ins` with the enrolled agent UUID and supplied plausible metadata containing a deliberately false hostname:

```json
{
  "hostname": "not-real-host",
  "operating_system": "macOS",
  "username": "user-test",
  "agent_version": "0.2.0",
  "architecture": "arm64"
}
```

The endpoint returned `200 OK` with the matching `agent_id` and `accepted: true`. I switched Swagger back to the operator credential and called `GET /api/v1/agents`. The agent inventory displayed the metadata submitted in the manual check-in, including `hostname: not-real-host`.

> [!NOTE]
> The server authenticated the reporting agent and validated the payload structure, but it could not independently verify whether the self-reported hostname was truthful. Authentication establishes which agent credential sent the data, not whether every reported value accurately describes the host.

I then called `GET /api/v1/audit-events`, filtering by the agent UUID and `event_type: agent.metadata_updated`. The event contained `operator_id: null` because an agent-authenticated request performed the check-in, and `task_id: null` because the update was unrelated to a task. Its separate `id` field identified the audit event itself.

Finally, I restarted the real agent with `uv run c2-relay-agent`. Its next authenticated check-in replaced the test values with the metadata collected by the real runtime. With Swagger authenticated as the operator, I called `GET /api/v1/agents` again and confirmed that the inventory displayed the restored metadata.

### Result Retry Idempotency

With the real agent running and Swagger authenticated as the operator, I created a `host.hostname` task through `POST /api/v1/tasks` using the idempotency key `poc-result-retry-0001`. The agent claimed and completed the task normally.

I retrieved the stored result through `GET /api/v1/tasks/{task_id}/result`. Its structure was:

```json
{
  "item": {
    "result": {
      "status": "completed",
      "task_id": "<task-id>",
      "agent_id": "<agent-id>",
      "completed_at": "<agent-completion-timestamp>",
      "output": {
        "kind": "host.hostname",
        "hostname": "<reported-hostname>"
      }
    },
    "received_at": "<server-received-timestamp>"
  }
}
```

I recorded both timestamps, switched Swagger from the operator credential to the agent credential, and called `POST /api/v1/agents/{agent_id}/results`. For the request body, I resubmitted an exact copy of the nested `item.result` object from the stored response:

```json
{
  "status": "completed",
  "task_id": "<same-task-id>",
  "agent_id": "<same-agent-id>",
  "completed_at": "<exact-original-completed-at>",
  "output": {
    "kind": "host.hostname",
    "hostname": "<exact-original-hostname>"
  }
}
```

The retry returned `200 OK`. The server-owned `received_at` field was not part of the submitted payload.

I switched Swagger back to the operator credential and retrieved the result again through `GET /api/v1/tasks/{task_id}/result`. The response still contained the original `received_at` timestamp rather than a new retry timestamp, confirming that the stored result had not been replaced.

Finally, I called `GET /api/v1/audit-events` with the task ID and `event_type: task.completed`. The response returned `total: 1`, showing that the retry did not create another completion event.

> [!NOTE]
> This PoC demonstrates that the API safely accepts an identical result retry without duplicating the stored result or its audit history.

### Pending Result Recovery

With both the server and agent runtimes stopped, I changed `C2_RELAY_TASK_LEASE_SECONDS` in `.env` from `30` to `300`, then restarted only the server. The longer lease provided enough time to perform the manual recovery steps.

Using the operator credential in Swagger, I created a `host.hostname` task through `POST /api/v1/tasks` with the idempotency key `poc-pending-result-0001`. I recorded the returned task ID and prepared the following command in the `input` pane without running it yet:

```bash
uv run python -c '
import socket
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from c2_relay.agent.identity import IdentityStore
from c2_relay.agent.results import PendingResultStore
from c2_relay.models import ActionSuccess, HostnameOutput, TaskId

identity = IdentityStore(Path("agent-state.json")).load()
assert identity is not None

result = ActionSuccess(
    task_id=TaskId(UUID("<task-id>")),
    agent_id=identity.agent_id,
    completed_at=datetime.now(UTC),
    output=HostnameOutput(hostname=socket.gethostname()),
)
PendingResultStore(Path("pending-result.json")).save(result)
print("pending result saved")
'
```

I replaced `<task-id>` with the returned task ID and left the command ready. In Swagger, I replaced the operator credential with the agent credential.

> [!IMPORTANT]
> The time-sensitive portion begins when the following task-polling request claims the task. From that point, the pending result must reach the server before the 300-second lease expires.

I called `GET /api/v1/agents/{agent_id}/tasks/next` once and confirmed that the task entered the `claimed` state. This started the 300-second lease window. I immediately ran the prepared command, which printed `pending result saved`, and then used `ls -l pending-result.json` to confirm that the file existed with read and write permissions for its owner only.

I restarted the agent. During startup, it detected the saved result and automatically submitted it through `POST /api/v1/agents/{agent_id}/results`. The server returned `200 OK`, and the agent removed `pending-result.json` only after the submission succeeded.

> [!NOTE]
> The time-sensitive portion ended when the server accepted the result with `200 OK`. At that point, the task was completed and its lease no longer needed to remain active.

I switched Swagger back to the operator credential and called `GET /api/v1/tasks/{task_id}`. The response showed `status: completed` and `lease_expires_at: null`. I then called `GET /api/v1/tasks/{task_id}/result` and confirmed that the automatically submitted result had been stored.

To restore the normal configuration for the next PoC, I changed `C2_RELAY_TASK_LEASE_SECONDS` in `.env` back to `30`. This setting required a server restart before taking effect.

### Localhost Authentication Throttling

After restoring the task lease to 30 seconds, I stopped the agent and restarted the server. The restart applied the configuration change and cleared the process-local authentication-failure history.

To test rate limiting for repeated authentication failures, I ran the following command in the `input` pane:

```bash
for i in {1..11}; do
  curl -sS -o /dev/null \
    -H 'Authorization: Bearer deliberately-invalid' \
    -w "request ${i}: HTTP %{http_code}\n" \
    http://127.0.0.1:8000/api/v1/agents
done
```

Requests 1 through 10 returned `401 Unauthorized`. The 11th returned `429 Too Many Requests`, showing that the process-local limiter had reached its configured threshold for the localhost peer.

I immediately made one more invalid authenticated request with response headers enabled:

```bash
curl -i \
  -H 'Authorization: Bearer deliberately-invalid' \
  http://127.0.0.1:8000/api/v1/agents
```

The request was still inside the throttling window and returned:

```http
HTTP/1.1 429 Too Many Requests
date: Thu, 03 Sep 2026 15:48:54 GMT
server: uvicorn
retry-after: 51
content-length: 45
content-type: application/json

{"detail":"too many authentication failures"}
```

The `Retry-After: 51` header reported the remaining number of seconds before another authentication attempt from the same peer could be processed normally.

I then called the unauthenticated health endpoint:

```bash
curl -i http://127.0.0.1:8000/api/v1/health
```

It returned `200 OK`, confirming that the service remained available while authenticated access from localhost was throttled. I ran all three commands within a short interval so the invalid requests remained inside the same limiter window.

<details>
<summary>Observed server log</summary>

```text
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 401 Unauthorized
authentication failed method='GET' path='/api/v1/agents' peer='127.0.0.1'
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 401 Unauthorized
authentication failed method='GET' path='/api/v1/agents' peer='127.0.0.1'
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 401 Unauthorized
authentication failed method='GET' path='/api/v1/agents' peer='127.0.0.1'
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 401 Unauthorized
authentication failed method='GET' path='/api/v1/agents' peer='127.0.0.1'
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 401 Unauthorized
authentication failed method='GET' path='/api/v1/agents' peer='127.0.0.1'
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 401 Unauthorized
authentication failed method='GET' path='/api/v1/agents' peer='127.0.0.1'
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 401 Unauthorized
authentication failed method='GET' path='/api/v1/agents' peer='127.0.0.1'
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 401 Unauthorized
authentication failed method='GET' path='/api/v1/agents' peer='127.0.0.1'
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 401 Unauthorized
authentication failed method='GET' path='/api/v1/agents' peer='127.0.0.1'
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 401 Unauthorized
authentication failed method='GET' path='/api/v1/agents' peer='127.0.0.1'
authentication rate limit exceeded method='GET' path='/api/v1/agents' peer='127.0.0.1' retry_after=60
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 429 Too Many Requests
INFO:     127.0.0.1:<source_port> - "GET /api/v1/agents HTTP/1.1" 429 Too Many Requests
INFO:     127.0.0.1:<source_port> - "GET /api/v1/health HTTP/1.1" 200 OK
INFO:     127.0.0.1:<source_port> - "GET /api/v1/health HTTP/1.1" 200 OK
```

</details>

The invalid bearer value did not appear in the log. Authentication telemetry recorded only the method, request path, localhost peer address, and retry delay, preventing credentials from being exposed through routine failure logging.

## PoC Verification Summary

| PoC | Control | Expected result | Observed result |
| --- | --- | --- | --- |
| Streamed Request-Size Enforcement | Bounded request-body processing | A 65,537-byte chunked body is rejected before authentication and route parsing | `413 Content Too Large` with `request body too large` |
| Task Creation Idempotency | Duplicate task prevention | Identical keyed requests resolve to one task; reuse with a different payload is rejected | Same task UUID and one creation event; changed payload returned `409 Conflict` |
| Idempotent Task Cancellation | Repeat-safe state transition | Repeated cancellation returns the existing state without duplicate side effects | Every call returned `200 OK`; audit history retained one cancellation event |
| Expired Claim Recovery | Task lease recovery and stale-result rejection | An expired claim can be reclaimed; a result submitted after the renewed lease expires is rejected | Same task received a new lease; late result returned `409 Conflict` |
| Metadata Trust Boundary | Authenticated self-reporting without host attestation | Validly shaped metadata is accepted, but its truthfulness is not independently established | False hostname was stored and audited; the real agent later restored its metadata |
| Result Retry Idempotency | Duplicate result prevention | An identical result retry is accepted without replacing stored state or duplicating audit history | Retry returned `200 OK`; `received_at` was unchanged and one completion event remained |
| Pending Result Recovery | Durable local result retry | A protected pending result survives interruption and is submitted after agent restart | Owner-only file was submitted successfully, removed afterward, and task became completed |
| Localhost Authentication Throttling | Process-local failed-authentication limiter and log redaction | Repeated failures are throttled while health remains available and credentials stay out of logs | Ten `401` responses were followed by `429` and `Retry-After`; health returned `200 OK` |

## Related Documentation

- See the [README](../README.md) for current capabilities, operating constraints, and safety scope.
- See the [CHANGELOG](../CHANGELOG.md) for the changes and known limitations associated with v0.2.0.
