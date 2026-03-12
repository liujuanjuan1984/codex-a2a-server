# codex-a2a-serve

> **Turning Codex into a production-ready, stateful Agent API with REST/JSON-RPC endpoints, authentication, streaming, and session management.**
>
> **Tech Stack:** Python 3.11+ | FastAPI | A2A SDK | `uv` | `pytest`

`codex-a2a-serve` is an adapter layer that exposes Codex as an A2A service (FastAPI + A2A SDK). It provides:

- A2A HTTP+JSON (REST): `/v1/message:send`, `/v1/message:stream`,
  `GET /v1/tasks/{task_id}:subscribe`, and related endpoints
- A2A JSON-RPC: `POST /` (for standard methods and extensions such as session queries)

In practice, this service is a protocol bridge and security boundary: it maps A2A message/task semantics to Codex app-server JSON-RPC APIs, while adding authentication, observability, and session-continuation contracts.

> Important: `A2A_BEARER_TOKEN` is required for startup.
> See `docs/guide.md`.

## Security Boundary (Read First)

- In the current architecture, the `codex` process must read LLM provider API
  credentials (for example `GOOGLE_GENERATIVE_AI_API_KEY`).
- Because of that, an `codex agent` may leak sensitive environment values
  through prompt injection or indirect exfiltration patterns.
- Do not treat this deployment model as a hard guarantee that provider keys are
  inaccessible to agent behavior.
- This project is best suited for trusted/internal environments until a stronger
  token isolation model is implemented (for example tenant isolation, hosted
  proxy credentials, auditing, and rotation/revocation strategy).
- Within one `codex-a2a-serve` instance, all consumers operate on the same
  underlying Codex workspace/environment. It is not tenant-isolated by
  default.

Additional notes:

- The A2A layer enforces bearer-token authentication via `A2A_BEARER_TOKEN`.
- When `A2A_LOG_PAYLOADS=true`, payload logs may include request/response
  bodies. For `codex.sessions.*` JSON-RPC queries, request/response body
  logging is intentionally suppressed to reduce chat-history exposure risk.
- Deployment-side LLM provider coverage and known gaps are documented in
  `docs/deployment.md` (`Current Provider Coverage and Gaps`).

## Capabilities

- Standard A2A chat: forwards `message:send` / `message:stream` to Codex.
- SSE streaming: `/v1/message:stream` emits incremental updates and then
  closes with `TaskStatusUpdateEvent(final=true)`. For detailed streaming
  contract and event semantics, see `docs/guide.md`.
- Token usage passthrough: normalized usage/cost stats are exposed at
  `metadata.shared.usage` (stream final status and non-streaming task metadata).
- Interrupt callback passthrough: when Codex emits `permission.asked` /
  `question.asked`, stream status events include `metadata.shared.interrupt`
  while provider-private raw details remain under `metadata.codex.interrupt`.
- Re-subscribe after disconnect: `GET /v1/tasks/{task_id}:subscribe`
  (available while the task is not in a terminal state).
- Session continuation contract: clients can explicitly bind to an existing
  Codex session via `metadata.shared.session.id`.
- Codex session query extension (JSON-RPC):
  `codex.sessions.list` / `codex.sessions.messages.list`.

## Transport Notes

- The service keeps dual-stack transport support: HTTP+JSON (REST routes) and JSON-RPC (`POST /`).
- Agent Card sets `preferredTransport=HTTP+JSON` and still declares JSON-RPC via `additionalInterfaces`.
- Request payloads are transport-specific and must not be mixed:
  - REST (`/v1/message:send`): typically `message.content` with role values like `ROLE_USER`
  - JSON-RPC (`method=message/send`): `params.message.parts` with role values `user` / `agent`

## Quick Start

1. Ensure Codex CLI is available (`codex` in `PATH`), or set `CODEX_CLI_BIN`:

```bash
codex --version
```

2. Install dependencies:

```bash
uv sync --all-extras
```

3. Start A2A service:

```bash
A2A_BEARER_TOKEN=dev-token uv run codex-a2a-serve
```

Default listen address: `http://127.0.0.1:8000`

A2A Agent Card: `http://127.0.0.1:8000/.well-known/agent-card.json`

Minimal request example:

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer dev-token' \
  -d '{
    "message": {
      "messageId": "msg-1",
      "role": "ROLE_USER",
      "content": [{"text": "Explain what this repository does."}]
    }
  }'
```

## Key Configuration

For full configuration, see `docs/guide.md`. Most commonly used options:

- `CODEX_CLI_BIN`: Codex CLI binary path (default: `codex`)
- `CODEX_APP_SERVER_LISTEN`: Codex app-server transport (default: `stdio://`)
- `CODEX_MODEL`: default model for `thread/start` (default: `gpt-5.1-codex`)
- `CODEX_MODEL_ID`: optional per-turn model override for `turn/start`
- `CODEX_DIRECTORY`: default `cwd` (optional). Clients may pass
  `metadata.codex.directory` only when `A2A_ALLOW_DIRECTORY_OVERRIDE=true`
  and the path stays inside the allowed workspace.
- `CODEX_TIMEOUT_STREAM`: optional timeout for streaming send path.
  Unset means no explicit stream timeout (the turn waits until completion).
- `A2A_BEARER_TOKEN`: required bearer token for authentication
- `A2A_PUBLIC_URL`: externally reachable URL prefix exposed in Agent Card
- `A2A_PROJECT`: optional project label injected into Agent Card metadata/examples
- `A2A_STREAMING`: enables SSE streaming (default: `true`)
- `A2A_SESSION_CACHE_TTL_SECONDS` / `A2A_SESSION_CACHE_MAXSIZE`:
  in-memory `(identity, contextId) -> session_id` mapping cache settings

Compatibility note:
- Legacy `OPENCODE_*` env keys are still accepted as fallback aliases.

## Session Continuation Contract

To continue an existing Codex conversation, pass this metadata key on every invoke request:

- `metadata.shared.session.id`: target Codex session ID (for example
  `ses_xxx`)

Server behavior:

- If provided, the server sends the message to the specified session.
- If omitted, the server creates a new session and caches
  `(identity, contextId) -> session_id` with TTL and max-size bounds.

Example:

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer dev-token' \
  -d '{
    "message": {
      "messageId": "msg-continue-1",
      "role": "ROLE_USER",
      "content": [{"text": "Continue our previous conversation and summarize the last conclusion."}]
    },
    "metadata": {
      "shared": {
        "session": {
          "id": "<session_id>"
        }
      }
    }
  }'
```

## Codex Session Query (A2A Extension via JSON-RPC)

The service exposes Codex session list/history queries through A2A extension methods on the JSON-RPC endpoint (`POST /`), without introducing custom REST endpoints.

- Auth: same `Authorization: Bearer <token>`
- Result: `result.items` always contains A2A standard objects
  (Task for session list, Message for history)
- Shared session metadata is exposed at `metadata.shared.session`
- Codex raw records are preserved in `metadata.codex.raw`
- Interrupt callback methods:
  - `a2a.interrupt.permission.reply`
  - `a2a.interrupt.question.reply`
  - `a2a.interrupt.question.reject`

List sessions (`codex.sessions.list`):

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer dev-token' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "codex.sessions.list",
    "params": {"limit": 20}
  }'
```

List messages in a session (`codex.sessions.messages.list`):

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer dev-token' \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "codex.sessions.messages.list",
    "params": {
      "session_id": "<session_id>",
      "limit": 50
    }
  }'
```

## Documentation

- Script entry guide (init/deploy/local/uninstall):
  [`scripts/README.md`](scripts/README.md)
- Usage guide (configuration, auth, streaming, client examples):
  [`docs/guide.md`](docs/guide.md)
- Systemd multi-instance deployment details:
  [`docs/deployment.md`](docs/deployment.md)

## License

This project is licensed under the Apache License 2.0.
See [`LICENSE`](LICENSE).

## Development & Validation

CI (`.github/workflows/ci.yml`) runs the same baseline checks on PRs and `main` pushes.

```bash
uv run pre-commit run --all-files
uv run pytest
```
