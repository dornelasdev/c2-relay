# c2-relay

A security-focused Python framework for authenticated agent coordination, task management,
and operational telemetry in authorized environments.

> [!IMPORTANT]
> c2-relay is under active development and is not ready for operational use. Use it only on
> systems you own or are explicitly authorized to test.

## Development

Requirements:

- Python 3.12 through 3.14
- [uv](https://docs.astral.sh/uv/)

Install the project and its development dependencies:

```bash
uv sync
```

Run the local quality checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Apply database migrations before starting the API:

```bash
uv run alembic upgrade head
uv run c2-relay-server
```

The server binds to `127.0.0.1:8000` by default. Copy `.env.example` to `.env` and configure a
high-entropy `C2_RELAY_BOOTSTRAP_TOKEN` to enable agent enrollment. Provision an operator with
`uv run c2-relay-operator create --name "Local Operator"`; its one-time credential authorizes task
creation. The API must be placed behind TLS before use across a network.

Start an enrolled or bootstrap-configured agent in a separate terminal:

```bash
uv run c2-relay-agent
```

The agent persists its issued identity and any result awaiting delivery in owner-only local JSON
files. Its runtime supports only the three documented read-only host actions; it does not invoke
a shell.

## Platform support

Version `0.1.0` supports Linux and macOS. Host-information actions use cross-platform Python
APIs, but local credential protection currently relies on POSIX `0600` file permissions.

Windows is not considered securely supported yet. Equivalent Windows support requires native
ACL enforcement or integration with the operating system credential store.

See [`docs/local-runbook.md`](docs/local-runbook.md) for the complete local verification flow.

## License

Licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE).
