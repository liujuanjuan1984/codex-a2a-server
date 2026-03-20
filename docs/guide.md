# Usage Guide

This guide covers runtime configuration, transport contracts,
streaming/session/interrupt behavior, and client examples.
It is the canonical document for implementation-level protocol contracts;
[README.md](../README.md) stays at overview level.

## Transport Contracts

- The service supports both transports:
  - HTTP+JSON (REST endpoints such as `/v1/message:send`)
  - JSON-RPC (`POST /`)
- Agent Card keeps `preferredTransport=HTTP+JSON` and also exposes JSON-RPC in `additional_interfaces`.
- Payload schema is transport-specific and should not be mixed:
  - REST send payload usually uses `message.content` and role values like `ROLE_USER`
  - JSON-RPC `message/send` payload uses `params.message.parts` and role values `user` / `agent`
- The JSON-RPC entrypoint now publishes an explicit wire contract for the
  supported method set and unsupported-method error shape.

## Wire Contract

The service publishes a machine-readable wire contract through Agent Card and
OpenAPI metadata.

Use it to answer:

- which JSON-RPC methods are part of the current A2A core baseline
- which JSON-RPC methods are custom extensions
- which methods are deployment-conditional rather than always available
- what error shape is returned for unsupported JSON-RPC methods

Current behavior:

- core JSON-RPC methods:
  - `message/send`
  - `message/stream`
  - `tasks/get`
  - `tasks/cancel`
  - `tasks/resubscribe`
- core HTTP endpoints:
  - `/v1/message:send`
  - `/v1/message:stream`
  - `/v1/tasks/{id}:subscribe`
- extension JSON-RPC methods are declared separately from the core baseline
- `codex.sessions.shell` becomes deployment-conditional when
  `A2A_ENABLE_SESSION_SHELL=false`

Unsupported method contract:

- JSON-RPC error code: `-32601`
- error message: `Unsupported method: <method>`
- error data fields:
  - `type=METHOD_NOT_SUPPORTED`
  - `method`
  - `supported_methods`
  - `protocol_version`

Consumer guidance:

- Discover the current method set from Agent Card / OpenAPI before calling
  custom JSON-RPC methods.
- Treat `supported_methods` in `error.data` as the runtime truth for the
  current deployment, especially when a deployment-conditional method is
  disabled.

## Compatibility Profile

The service publishes a machine-readable compatibility profile through Agent
Card and OpenAPI metadata. Its purpose is to declare:

- the stable A2A core interoperability baseline
- which shared extensions are intended to be reused across this repo family
- which Codex-specific JSON-RPC methods are product-specific extensions
- which extension surfaces are required runtime metadata contracts
- which methods are deployment-conditional rather than always available

Current profile shape:

- `profile_id=codex-a2a-single-tenant-coding-v1`
- deployment profile:
  - `id=single_tenant_shared_workspace`
  - `single_tenant=true`
  - `shared_workspace_across_consumers=true`
  - `tenant_isolation=none`
- runtime features:
  - `directory_binding.allow_override=true|false`
  - `directory_binding.scope=workspace_root_or_descendant|workspace_root_only`
  - `session_shell.enabled=true|false`
  - `session_shell.availability=enabled|disabled`
  - `interrupts.request_ttl_seconds=<int>`
  - `service_features.streaming.enabled=true`
  - `service_features.health_endpoint.enabled=true|false`
  - `execution_environment.sandbox.mode=unknown|read-only|workspace-write|danger-full-access`
  - `execution_environment.sandbox.filesystem_scope=unknown|none|workspace_root|workspace_root_or_descendant|configured_roots|full_filesystem`
  - `execution_environment.network.access=unknown|disabled|enabled|restricted`
  - `execution_environment.approval.policy=unknown|never|on-request|on-failure|untrusted-only`
  - `execution_environment.write_access.scope=unknown|none|workspace_root|workspace_root_or_descendant|configured_roots|full_filesystem`
- runtime context:
  - `project=<optional>`
  - `workspace_root=<optional>`
  - `provider_id=<optional>`
  - `model_id=<optional>`
  - `agent=<optional>`
  - `variant=<optional>`
- core JSON-RPC methods:
  - `message/send`
  - `message/stream`
  - `tasks/get`
  - `tasks/cancel`
  - `tasks/resubscribe`
- core HTTP endpoints:
  - `/v1/message:send`
  - `/v1/message:stream`
  - `/v1/tasks/{id}:subscribe`

Retention guidance:

- Treat core methods as the generic client interoperability baseline.
- Treat this deployment as a single-tenant, shared-workspace coding profile.
- Treat shared session-binding and streaming metadata contracts as required for
  the current deployment model; they are not optional documentation-only hints.
- Treat `urn:a2a:*` extension URIs in this repository as shared extension
  conventions used across this repo family, not as claims that they are part
  of the A2A core baseline.
- Treat `a2a.interrupt.*` methods as shared extensions.
- Treat `codex.*` methods and `metadata.codex.directory` as Codex-specific
  extensions or provider-private operational surfaces rather than portable A2A
  baseline capabilities.
- Treat `codex.sessions.shell` as deployment-conditional. Discover it from the
  declared compatibility profile and extension contracts before calling it.
- Treat `execution_environment.*` as deployment-configured discovery metadata.
  It does not promise per-request snapshots of temporary approvals, escalations,
  or host-side runtime mutations.

Current implementation note:

- The compatibility profile is declarative. It does not introduce a global
  runtime `core-only` switch.
- This is intentional: current shared session/stream/interrupt behavior is part
  of the deployed interoperability contract, so a blanket runtime profile split
  would be misleading without broader wire-level changes.

## Environment Variables

- `CODEX_CLI_BIN`: Codex CLI binary path, default `codex`
- `CODEX_APP_SERVER_LISTEN`: Codex app-server listen target, default `stdio://`
- `CODEX_MODEL`: default model passed to `thread/start`, default `gpt-5.1-codex`
- `CODEX_MODEL_ID`: per-turn model override passed to `turn/start` (optional)
- `CODEX_MODEL_REASONING_EFFORT`: explicit reasoning effort override passed to
  Codex CLI app-server via `-c model_reasoning_effort=...` (optional)
- `CODEX_WORKSPACE_ROOT`: default Codex workspace root (optional)
- `CODEX_PROVIDER_ID`: deployment metadata only (optional)
- `CODEX_AGENT`: deployment metadata only (optional)
- `CODEX_VARIANT`: deployment metadata only (optional)
- `CODEX_TIMEOUT`: request timeout in seconds, default `120`
- `CODEX_TIMEOUT_STREAM`: streaming turn timeout in seconds (optional);
  unset means no explicit stream timeout for the streaming send path

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
- `A2A_ENABLE_HEALTH_ENDPOINT`: enable the authenticated lightweight `/health` probe, default `true`
- `A2A_ENABLE_SESSION_SHELL`: expose `codex.sessions.shell` on JSON-RPC extensions, default `true`
- `A2A_LOG_LEVEL`: `DEBUG/INFO/WARNING/ERROR`, default `INFO`
- `A2A_LOG_PAYLOADS`: log A2A/Codex payload bodies, default `false`
- `A2A_LOG_BODY_LIMIT`: payload log body size limit, default `0` (no truncation)
- `A2A_DOCUMENTATION_URL`: optional URL exposed via Agent Card
  `documentationUrl`
- `A2A_ALLOW_DIRECTORY_OVERRIDE`: allow `metadata.codex.directory` overrides
  within the configured workspace boundary, default `true`
- `A2A_SESSION_CACHE_TTL_SECONDS`: in-memory TTL for
  `(identity, contextId) -> Codex session_id`, default `3600`
- `A2A_SESSION_CACHE_MAXSIZE`: max cache entries, default `10000`
- `A2A_CANCEL_ABORT_TIMEOUT_SECONDS`: how long `tasks/cancel` waits for
  in-flight execution/session-create cleanup after issuing cancellation,
  default `1.0`; `0` means best-effort cancel without waiting
- `A2A_STREAM_SSE_PING_SECONDS`: transport-level SSE keepalive interval,
  default `10` (integer seconds)
- `A2A_STREAM_IDLE_DIAGNOSTIC_SECONDS`: threshold before the server emits a
  stream idle diagnostic log, default `60.0`
- `A2A_INTERRUPT_REQUEST_TTL_SECONDS`: TTL for pending interrupt callbacks
  before they become expired, default `3600`
- `A2A_EXECUTION_SANDBOX_MODE`: declarative sandbox mode for machine-readable
  discovery, default `unknown`
- `A2A_EXECUTION_SANDBOX_FILESYSTEM_SCOPE`: optional filesystem scope override
  for machine-readable discovery
- `A2A_EXECUTION_SANDBOX_WRITABLE_ROOTS`: optional comma-separated writable
  root list for machine-readable discovery
- `A2A_EXECUTION_NETWORK_ACCESS`: declarative network access policy for
  machine-readable discovery, default `unknown`
- `A2A_EXECUTION_NETWORK_ALLOWED_DOMAINS`: optional comma-separated allowlist
  exposed only when safe to disclose
- `A2A_EXECUTION_APPROVAL_POLICY`: declarative approval policy for
  machine-readable discovery, default `unknown`
- `A2A_EXECUTION_APPROVAL_ESCALATION_BEHAVIOR`: optional declarative approval
  escalation behavior override
- `A2A_EXECUTION_WRITE_ACCESS_SCOPE`: optional declarative write-access scope
  override for machine-readable discovery
- `A2A_EXECUTION_WRITE_OUTSIDE_WORKSPACE`: optional declarative override for
  whether write access extends outside the workspace

Configuration note:
- The service configuration layer only accepts `CODEX_*` names for Codex-facing settings.

Codex prerequisite note:
- `codex-a2a-server` assumes the local `codex` runtime is already usable.
- Install and verify the `codex` CLI itself before starting this server.
- Provider selection, login state, and upstream API keys remain Codex-side prerequisites.
- Service startup fails fast when the local `codex` runtime is missing or cannot initialize.

## Released CLI Self-Start

For a single user or an existing workspace root, prefer the published CLI
instead of repository scripts. The abbreviated quick-start stays in
[README.md](../README.md); this section keeps the fuller runtime example and
operational notes.

Install once:

```bash
uv tool install codex-a2a-server
```

Before starting the runtime:

- verify `codex` itself is installed and available on `PATH` (or set `CODEX_CLI_BIN`)
- verify your Codex provider/model/auth setup already works outside this repository
- `codex-a2a-server` does not provision Codex providers, login state, or API keys

Run against a workspace root:

```bash
export A2A_BEARER_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(24))')"
A2A_HOST=127.0.0.1 \
A2A_PORT=8000 \
A2A_PUBLIC_URL=http://127.0.0.1:8000 \
CODEX_WORKSPACE_ROOT=/abs/path/to/workspace \
CODEX_MODEL_ID=gpt-5.1-codex \
CODEX_TIMEOUT=300 \
codex-a2a-server
```

Notes:

- `CODEX_WORKSPACE_ROOT` should point at the workspace root you want Codex to operate in.
- `codex-a2a-server` launches the Codex app-server subprocess itself; no
  separate `codex serve` step is required.
- Upgrade the installed CLI with `uv tool upgrade codex-a2a-server`.

## Source-Based Development Start

Use the source tree directly only for development, debugging, or validation of
unreleased changes:

```bash
uv sync --all-extras
export A2A_BEARER_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(24))')"
CODEX_WORKSPACE_ROOT=/abs/path/to/workspace uv run codex-a2a-server
```

This path is for contributors. End users should prefer the released CLI path
described first in [README.md](../README.md) and above in this guide.

## Service Behavior

### Health, Auth, and Deployment Boundary

- `GET /health` is a lightweight authenticated status probe. It requires the
  same `Authorization: Bearer <token>` header as other protected endpoints and
  returns service status plus a structured `profile` summary; it does not call
  upstream Codex.
- Requests require `Authorization: Bearer <token>`; otherwise `401` is
  returned. Agent Card endpoints are public.
- Within one `codex-a2a-server` instance, all consumers share the same
  underlying Codex workspace/environment. This deployment model is not
  tenant-isolated by default.

### Session and Task Behavior

- The service forwards A2A `message:send` to Codex session/message calls.
- Streaming is always enabled for this service surface. `/v1/message:stream`
  and JSON-RPC `message/stream` are compatibility-sensitive core capabilities
  rather than deployment-time toggles.
- `codex.sessions.shell` is a session-scoped shell control method for
  ownership, attribution, and traceability. It keeps `session_id` in the A2A
  contract, but the underlying execution still uses Codex `command/exec`
  rather than resuming or creating an upstream Codex thread.
- Session query projections currently use the upstream Codex `session_id` as
  the A2A `contextId`. This is intentional for the current deployment model:
  `contextId` and `metadata.shared.session.id` refer to the same upstream
  session identity, and the contract declares that equality explicitly.
- Task state defaults to `input-required` to support multi-turn interactions.
- Non-streaming requests return a `Task` directly.
- Non-streaming `message:send` responses may include normalized token usage at
  `Task.metadata.shared.usage` with the same field schema.

### Streaming and Interrupt Contract

- Streaming (`/v1/message:stream`) emits incremental
  `TaskArtifactUpdateEvent` and then `TaskStatusUpdateEvent(final=true)`.
- Stream artifacts carry `artifact.metadata.shared.stream.block_type` with
  values `text`, `reasoning`, and `tool_call`.
- The published `urn:a2a:stream-hints/v1` contract also declares the emitted
  A2A part type per block: `text` and `reasoning` use `TextPart`, while
  `tool_call` uses `DataPart`.
- All chunks share one stream artifact ID and preserve original timeline via
  `artifact.metadata.shared.stream.sequence`. Timeline identity fields such as
  `message_id`, `event_id`, and `source` are emitted under
  `metadata.shared.stream`.
- Session projections are normalized under `metadata.shared.session`, with
  `id` as the canonical field and optional `title` when the upstream surface
  provides one. The corresponding leaf fields are
  `metadata.shared.session.id` and `metadata.shared.session.title`.
- A final snapshot is emitted only when stream chunks did not already produce
  the same final text.
- Stream routing is schema-first: the service classifies chunks primarily by
  Codex `part.type` plus `part_id` state rather than inline text markers.
- `message.part.delta` and `message.part.updated` are merged per `part_id`;
  out-of-order deltas are buffered and replayed when the corresponding
  `part.updated` arrives.
- `text` and `reasoning` chunks are emitted as `TextPart`, while `tool_call`
  chunks are emitted as `DataPart` with a normalized structured payload.
- Legacy stringified JSON tool payloads are rejected; the stream contract only
  accepts structured `DataPart(data={...})` payloads.
- To avoid character-level event floods, the service performs light server-side
  aggregation before emitting `text` and `reasoning` updates: `text` flushes at
  `120 chars or 200ms`, `reasoning` flushes at `240 chars or 350ms`, and both
  flush immediately on block switches, `tool_call`, and request completion
  boundaries.
- Final status event metadata may include normalized token usage at
  `metadata.shared.usage` with fields like `input_tokens`, `output_tokens`,
  `total_tokens`, optional `metadata.shared.usage.reasoning_tokens`,
  `metadata.shared.usage.cache_tokens.read_tokens`,
  `metadata.shared.usage.cache_tokens.write_tokens`,
  `metadata.shared.usage.raw`, and optional `cost`.
- Interrupt lifecycle is explicit:
  - asked events (`permission.asked` / `question.asked`) are mapped to
    `TaskStatusUpdateEvent(final=false, state=input-required)` with
    `metadata.shared.interrupt.phase=asked`
  - resolved events (`permission.replied` / `question.replied` /
    `question.rejected`) are mapped to
    `TaskStatusUpdateEvent(final=false, state=working)` with
    `metadata.shared.interrupt.phase=resolved` and
    `metadata.shared.interrupt.resolution=replied|rejected`
- Duplicate or unknown resolved events are suppressed by `request_id`.
- For Codex app-server approval and `tool/requestUserInput` requests,
  user-visible approval/question details are normalized into
  `metadata.shared.interrupt.details`, including readable `display_message`,
  resolved `patterns`, and `questions` when available.
- HTTP streaming responses send transport-level SSE ping comments on a
  configurable interval without adding synthetic A2A business events.
- Interrupt status events no longer mirror the asked payload under
  `metadata.codex.interrupt`; downstream consumers should treat
  `metadata.shared.interrupt` as the single interrupt rendering contract.

### Tool Call Payload Contract

- The same shape is published in the machine-readable streaming extension
  contract under `tool_call_payload_contract`.

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
### Directory and Error Handling

- For validation failures, missing context (`task_id`/`context_id`), or
  internal errors, the service attempts to return standard A2A failure events
  via `event_queue`.
- Failure events include concrete error details with `failed` state.
- Clients can pass `metadata.codex.directory`, but it must stay inside
  `${CODEX_WORKSPACE_ROOT}` (or service runtime root if not configured).
- All paths are normalized with `realpath` to prevent `..` or symlink boundary
  bypass.
- If `A2A_ALLOW_DIRECTORY_OVERRIDE=false`, only the default directory is
  accepted.
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
  - limit pagination defaults to `20` items and rejects values above `100`
  - pagination behavior is mixed: `codex.sessions.list` forwards `limit` upstream,
    while `codex.sessions.messages.list` applies the limit locally
  - `codex.sessions.messages.list` enforces `limit` locally after mapping the
    upstream thread history into A2A messages, keeping the most recent N messages
    while preserving their original order
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
      "limit": 20
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
