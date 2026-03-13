# Usage Guide

This guide is the technical reference for day-to-day integration. It focuses on
runtime configuration, transport contracts, streaming/session/interrupt
behavior, and client examples.

If you are looking for project value, architecture, or documentation entry
navigation, start from [README.md](../README.md) instead.

## Transport Contracts

- The service supports both transports:
  - HTTP+JSON (REST endpoints such as `/v1/message:send`)
  - JSON-RPC (`POST /`)
- Agent Card keeps `preferredTransport=HTTP+JSON` and also exposes JSON-RPC in `additional_interfaces`.
- Payload schema is transport-specific and should not be mixed:
  - REST send payload usually uses `message.content` and role values like `ROLE_USER`
  - JSON-RPC `message/send` payload uses `params.message.parts` and role values `user` / `agent`

## Environment Variables

- `CODEX_CLI_BIN`: Codex CLI binary path, default `codex`
- `CODEX_APP_SERVER_LISTEN`: Codex app-server listen target, default `stdio://`
- `CODEX_MODEL`: default model passed to `thread/start`, default `gpt-5.1-codex`
- `CODEX_MODEL_ID`: per-turn model override passed to `turn/start` (optional)
- `CODEX_MODEL_REASONING_EFFORT`: explicit reasoning effort override passed to
  Codex CLI app-server via `-c model_reasoning_effort=...` (optional)
- `CODEX_DIRECTORY`: default Codex working directory (optional)
- `CODEX_PROVIDER_ID`: deployment metadata only (optional)
- `CODEX_AGENT`: deployment metadata only (optional)
- `CODEX_SYSTEM`: reserved compatibility field (optional)
- `CODEX_VARIANT`: deployment metadata only (optional)
- `CODEX_TIMEOUT`: request timeout in seconds, default `120`
  (systemd deployment template may write `300` by default)
- `CODEX_TIMEOUT_STREAM`: streaming turn timeout in seconds (optional);
  unset means no explicit stream timeout for the streaming send path
- `CODEX_BASE_URL`: reserved compatibility field for legacy HTTP mode; not used by app-server mode

- `A2A_PUBLIC_URL`: externally reachable A2A URL prefix,
  default `http://127.0.0.1:8000`
- `A2A_PROJECT`: optional project label injected into Agent Card extensions and examples
- `A2A_TITLE`: agent name, default `Codex A2A`
- `A2A_DESCRIPTION`: agent description
- `A2A_VERSION`: agent version
- `A2A_PROTOCOL_VERSION`: A2A protocol version, default `0.3.0`
- `A2A_HOST`: bind host, default `127.0.0.1`
- `A2A_PORT`: bind port, default `8000`
- `A2A_BEARER_TOKEN`: required; service fails fast if unset
- `A2A_STREAMING`: enable SSE streaming (`/v1/message:stream`), default `true`
- `A2A_LOG_LEVEL`: `DEBUG/INFO/WARNING/ERROR`, default `INFO`
- `A2A_LOG_PAYLOADS`: log A2A/Codex payload bodies, default `false`
- `A2A_LOG_BODY_LIMIT`: payload log body size limit, default `0` (no truncation)
- `A2A_DOCUMENTATION_URL`: optional URL exposed via Agent Card
  `documentationUrl`
- `A2A_OAUTH_AUTHORIZATION_URL`: OAuth2 authorization URL (declarative only)
- `A2A_OAUTH_TOKEN_URL`: OAuth2 token URL (declarative only)
- `A2A_OAUTH_METADATA_URL`: OAuth2 metadata URL (optional)
- `A2A_OAUTH_SCOPES`: comma-separated OAuth2 scopes (declarative only)
- `A2A_SESSION_CACHE_TTL_SECONDS`: in-memory TTL for
  `(identity, contextId) -> Codex session_id`, default `3600`
- `A2A_SESSION_CACHE_MAXSIZE`: max cache entries, default `10000`

Configuration note:
- The service configuration layer only accepts `CODEX_*` names for Codex-facing settings.

## Lightweight Deploy Inheritance

- `scripts/deploy_light.sh` is designed for local/interactive quick start.
- It preserves already-exported `CODEX_*` shell variables when present.
- By default it best-effort reads `~/.codex/config.toml` and inherits:
  - `model`
  - `model_reasoning_effort`
- Explicit instance parameters still win over shell / local Codex config:
  - `codex_model=...`
  - `codex_model_id=...`
  - `codex_model_reasoning_effort=...`
- Before launch, the script prints a Codex config summary so the effective
  model / reasoning combination is visible.
- Known high-risk combinations are blocked before startup. Currently this
  includes `reasoning_effort=xhigh` together with `gpt-5.1-codex*`.
- `start` and `restart` create a new timestamped log file under
  `logs/light/<instance>-YYYYMMDD-HHMMSS.log`.
- `logs/light/<instance>.log` is kept as a stable alias to the latest launch,
  so `tail -f logs/light/<instance>.log` remains a convenient default.
- `restart` means `stop` the existing PID first, then `start` a fresh process;
  it does not reload the old process in place. If the instance is not running,
  `restart` falls through to a fresh `start`.
- `status` reports the actual log file for the currently running instance and,
  when stopped, the most recent log path remembered by the script.

## Service Behavior

- The service forwards A2A `message:send` to Codex session/message calls.
- Task state defaults to `input-required` to support multi-turn interactions.
- Streaming (`/v1/message:stream`) emits incremental
  `TaskArtifactUpdateEvent` and then
  `TaskStatusUpdateEvent(final=true)`. Stream artifacts carry
  `artifact.metadata.shared.stream.block_type` with values
  `text` / `reasoning` / `tool_call`. All chunks share one stream
  artifact ID and preserve original timeline via
  `artifact.metadata.shared.stream.sequence`. Timeline identity fields such as
  `message_id`, `event_id`, and `source` are emitted under
  `metadata.shared.stream`. A final snapshot is only emitted when stream chunks
  did not already produce the same final text.
  Stream routing is schema-first: the service classifies chunks primarily by
  Codex `part.type` (plus `part_id` state) rather than inline text markers.
  `message.part.delta` and `message.part.updated` are merged per `part_id`;
  out-of-order deltas are buffered and replayed when the corresponding
  `part.updated` arrives. `text` and `reasoning` chunks are emitted as
  `TextPart`, while `tool_call` chunks are emitted as `DataPart` with a
  normalized structured payload. Tool payloads use `kind` to distinguish
  structured state updates from plain tool output text:
  `kind=state` carries fields such as `tool`, `call_id`, `status`, `input`,
  `output`, and `error`; `kind=output_delta` carries the raw text increment in
  `output_delta` and may also include `source_method`, `tool`, `call_id`, and
  `status`. Legacy stringified JSON tool payloads are rejected; the stream
  contract only accepts structured `DataPart(data={...})` payloads. To avoid
  character-level event floods, the
  service performs light server-side aggregation before emitting `text` and
  `reasoning` updates: `text` flushes at `120 chars or 200ms`, `reasoning`
  flushes at `240 chars or 350ms`, and both flush immediately on block
  switches, `tool_call`, and request completion boundaries. Final status event
  metadata may include
  normalized token usage at `metadata.shared.usage` with fields like
  `input_tokens`, `output_tokens`, `total_tokens`, and optional `cost`.
  Interrupt lifecycle is explicit: asked events (`permission.asked` /
  `question.asked`) are mapped to
  `TaskStatusUpdateEvent(final=false, state=input-required)` with
  `metadata.shared.interrupt.phase=asked`; resolved events
  (`permission.replied` / `question.replied` / `question.rejected`) are mapped
  to `TaskStatusUpdateEvent(final=false, state=working)` with
  `metadata.shared.interrupt.phase=resolved` and
  `metadata.shared.interrupt.resolution=replied|rejected`. Duplicate or unknown
  resolved events are suppressed by `request_id`. Provider-private raw
  interrupt payload is preserved under `metadata.codex.interrupt`.
  Non-streaming requests return a `Task` directly.
- `tool_call` payload contract:

| `kind` | Required fields | Optional fields | Notes |
| --- | --- | --- | --- |
| `state` | `kind` | `source_method`, `call_id`, `tool`, `status`, `title`, `subtitle`, `input`, `output`, `error` | Used for structured tool state snapshots. A payload that contains only `kind=state` is invalid and is suppressed. |
| `output_delta` | `kind`, `output_delta` | `source_method`, `call_id`, `tool`, `status` | Used for raw tool output text increments. `output_delta` is preserved verbatim and may contain spaces or trailing newlines. |

  `codex app-server` lifecycle events such as `item/started` and
  `item/completed` are normalized into `kind=state`; `item/*/outputDelta`
  notifications are normalized into `kind=output_delta`.

  Examples:

  ```json
  {"kind":"state","tool":"bash","call_id":"call-1","status":"running"}
  ```

  ```json
  {"kind":"output_delta","source_method":"commandExecution","tool":"bash","call_id":"call-1","status":"running","output_delta":"Passed\n"}
  ```
- Non-streaming `message:send` responses may include normalized token usage at
  `Task.metadata.shared.usage` with the same field schema.
- Requests require `Authorization: Bearer <token>`; otherwise `401` is
  returned. Agent Card endpoints are public.
- Within one `codex-a2a-server` instance, all consumers share the same
  underlying Codex workspace/environment. This deployment model is not
  tenant-isolated by default.
- Error handling:
  - For validation failures, missing context (`task_id`/`context_id`), or
    internal errors, the service attempts to return standard A2A failure events
    via `event_queue`.
  - Failure events include concrete error details with `failed` state.
- Directory validation and normalization:
  - Clients can pass `metadata.codex.directory`, but it must stay inside
    `${CODEX_DIRECTORY}` (or service runtime root if not configured).
  - All paths are normalized with `realpath` to prevent `..` or symlink
    boundary bypass.
  - If `A2A_ALLOW_DIRECTORY_OVERRIDE=false`, only the default directory is
    accepted.
- OAuth2 settings are currently declarative in Agent Card only; runtime token
  verification for OAuth2 is not implemented yet.
- Agent Card declares OAuth2 only when both
  `A2A_OAUTH_AUTHORIZATION_URL` and `A2A_OAUTH_TOKEN_URL` are set.

## Authentication Setup For Local Examples

For local development examples, prefer generating a temporary token once and
reusing the exported environment variable in the following commands:

```bash
export A2A_BEARER_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(24))')"
```

Then reference the token in request examples as:

```bash
-H "Authorization: Bearer ${A2A_BEARER_TOKEN}"
```

## Session Continuation Contract

To continue a historical Codex session, include this metadata key in each invoke request:

- `metadata.shared.session.id`: target Codex session ID

Server behavior:

- If provided, the request is sent to that exact Codex session.
- If omitted, a new session is created and cached by
  `(identity, contextId) -> session_id`.

Minimal example:

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H "Authorization: Bearer ${A2A_BEARER_TOKEN}" \
  -d '{
    "message": {
      "messageId": "msg-continue-1",
      "role": "ROLE_USER",
      "content": [{"text": "Continue the previous session and restate the key conclusion."}]
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

## Codex Session Query (A2A Extension)

This service exposes Codex session list and message-history queries via A2A JSON-RPC extension methods (default endpoint: `POST /`). No extra custom REST endpoint is introduced.

- Trigger: call extension methods through A2A JSON-RPC
- Auth: same `Authorization: Bearer <token>`
- Privacy guard: when `A2A_LOG_PAYLOADS=true`, request/response bodies are still
  suppressed for `method=codex.sessions.*`
- Endpoint discovery: prefer `additional_interfaces[]` with
  `transport=jsonrpc` from Agent Card
- Result format:
  - `result.items` is always an array of A2A standard objects
  - session list => `Task` with `status.state=completed`
  - message history => `Message`
  - canonical session metadata is exposed at `metadata.shared.session`
  - raw upstream payload is preserved at `metadata.codex.raw`
  - session title is available at `metadata.shared.session.title`

### Session List (`codex.sessions.list`)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H "Authorization: Bearer ${A2A_BEARER_TOKEN}" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "codex.sessions.list",
    "params": {"limit": 20}
  }'
```

### Session Messages (`codex.sessions.messages.list`)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H "Authorization: Bearer ${A2A_BEARER_TOKEN}" \
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

## Codex Interrupt Callback (A2A Extension)

When stream metadata reports an interrupt request at `metadata.shared.interrupt`,
clients can reply through JSON-RPC extension methods:

- asked lifecycle events expose `phase=asked`
- resolved lifecycle events expose `phase=resolved`
- resolved events may also expose `resolution=replied|rejected`

- `a2a.interrupt.permission.reply`
  - required: `request_id`
  - required: `reply` (`once` / `always` / `reject`)
  - optional: `message`
- `a2a.interrupt.question.reply`
  - required: `request_id`
  - required: `answers` (`Array<Array<string>>`)
- `a2a.interrupt.question.reject`
  - required: `request_id`

Permission reply example:

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H "Authorization: Bearer ${A2A_BEARER_TOKEN}" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "a2a.interrupt.permission.reply",
    "params": {
      "request_id": "<request_id>",
      "reply": "once"
    }
  }'
```

## Authentication Example (curl)

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H "Authorization: Bearer ${A2A_BEARER_TOKEN}" \
  -d '{
    "message": {
      "messageId": "msg-1",
      "role": "ROLE_USER",
      "content": [{"text": "Explain what this repository does."}]
    }
  }'
```

## JSON-RPC Send Example (curl)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H "Authorization: Bearer ${A2A_BEARER_TOKEN}" \
  -d '{
    "jsonrpc": "2.0",
    "id": 101,
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "msg-1",
        "role": "user",
        "parts": [{"kind": "text", "text": "Explain what this repository does."}]
      }
    }
  }'
```

## Streaming Re-Subscription (`subscribe`)

If an SSE connection drops, use `GET /v1/tasks/{task_id}:subscribe` to re-subscribe while the task is still non-terminal.

## Development Setup

```bash
uv run pre-commit install
```
