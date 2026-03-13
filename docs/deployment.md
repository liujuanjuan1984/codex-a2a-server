# Deployment Guide (systemd Multi-Instance)

This guide explains how to deploy Codex + A2A as isolated per-project instances (two processes per project) on one host while sharing core runtime artifacts.

For project overview and architecture positioning, use [README.md](../README.md)
and [Architecture Guide](architecture.md). This document is only for deployment,
runtime secret handling, and operational setup.

## Prerequisites

- `sudo` access (required for systemd units, users, and directories).
- Codex core installed in shared directory
  (default `/opt/.codex`; editable in `scripts/init_system.sh`).
- This repository available on host in shared directory
  (default `/opt/codex-a2a/codex-a2a-server`; editable in
  `scripts/init_system.sh`).
- A2A virtualenv prepared
  (default `${CODEX_A2A_DIR}/.venv/bin/codex-a2a-server`).
- `uv` Python pool prepared (default `/opt/uv-python`).
- systemd available.

> Shared path defaults come from top-level constants in
> `scripts/init_system.sh`. `deploy.sh` still supports environment-variable
> overrides; keep them consistent with actual paths.

## Optional System Bootstrap

To prepare host prerequisites in one step:

```bash
./scripts/init_system.sh
```

Script characteristics:

- idempotent: completed steps are skipped
- decoupled from `deploy.sh`: only prepares host/shared environment

Default behavior:

- installs base tools (`htop`, `vim`, `curl`, `wget`, `git`, `net-tools`,
  `lsblk`, `ca-certificates`) and `gh`
- installs Node.js >= 20 (`npm`/`npx`) via NodeSource or distro package
- installs `uv` (if missing), pre-downloads Python `3.10/3.11/3.12/3.13`
- creates shared directories (`/opt/.codex`, `/opt/codex-a2a`,
  `/opt/uv-python`, `/data/codex-a2a`)
- sets `/opt/uv-python` permission from `777` to recursive `755`
- fails if `systemctl` is unavailable
- clones this repository to shared path (HTTPS URL by default)
- creates A2A virtualenv via `uv sync --all-extras`

Notes:

- `init_system.sh` has no runtime arguments; edit top constants to change
  defaults.

## Directory Layout

Each project instance gets an isolated directory under `DATA_ROOT` (default `/data/codex-a2a/<project>`):

- `workspace/`: writable Codex workspace
- `config/`: root-only config directory for env files
- `logs/`: service logs
- `run/`: runtime files (reserved)

Default permissions:

- `DATA_ROOT`: `711` (traversable, not listable)
- project root + `workspace` + `logs` + `run`: `700`
- `config/`: `700` (root-only), env files `600`

## Quick Deploy

Default behavior:

- `ENABLE_SECRET_PERSISTENCE=false` by default.
- In that default mode, deploy scripts do **not** write `GH_TOKEN`,
  `A2A_BEARER_TOKEN`, or provider keys to disk.
- The script expects operators to pre-provision root-only runtime secret files:
  - `config/codex.auth.env`
  - `config/a2a.secret.env`
  - `config/codex.secret.env` (optional provider keys)
- If those files are missing, the first deploy attempt creates `*.example`
  templates under `config/` and stops before services are started.

Secure default workflow (recommended):

1. Bootstrap project directories and example files:

```bash
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_host=127.0.0.1
```

2. Populate runtime secret files as `root` using the generated templates:

```bash
sudo cp /data/codex-a2a/alpha/config/codex.auth.env.example /data/codex-a2a/alpha/config/codex.auth.env
sudo cp /data/codex-a2a/alpha/config/a2a.secret.env.example /data/codex-a2a/alpha/config/a2a.secret.env
sudoedit /data/codex-a2a/alpha/config/codex.auth.env
sudoedit /data/codex-a2a/alpha/config/a2a.secret.env
```

3. Re-run deploy:

```bash
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_host=127.0.0.1
```

Explicit persistence opt-in (legacy-style one-step deploy):

```bash
read -rsp 'GH_TOKEN: ' GH_TOKEN; echo
read -rsp 'A2A_BEARER_TOKEN: ' A2A_BEARER_TOKEN; echo
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_host=127.0.0.1
```

HTTPS public URL example:

```bash
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_host=127.0.0.1 a2a_public_url=https://a2a.example.com
```

Supported CLI keys (case-insensitive): `project`/`project_name`, `data_root`, `a2a_port`, `a2a_host`, `a2a_public_url`, `a2a_streaming`, `a2a_log_level`, `a2a_log_payloads`, `a2a_log_body_limit`, `codex_provider_id`, `codex_model_id`, `repo_url`, `repo_branch`, `codex_timeout`, `codex_timeout_stream`, `git_identity_name`, `git_identity_email`, `enable_secret_persistence`, `update_a2a`, `force_restart`.

Runtime secret requirements:

- `GH_TOKEN` must be available to the `codex@.service` runtime
- `A2A_BEARER_TOKEN` must be available to the `codex-a2a@.service` runtime
- By default these are expected from pre-provisioned root-only secret files
- If `ENABLE_SECRET_PERSISTENCE=true`, deploy writes them into those files

Recommended style: capture secret values without echo and pass non-secret overrides as CLI keys:

```bash
read -rsp 'GH_TOKEN: ' GH_TOKEN; echo
read -rsp 'A2A_BEARER_TOKEN: ' A2A_BEARER_TOKEN; echo
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh \
  project=alpha \
  data_root=/data/codex-a2a \
  a2a_log_payloads=true \
  a2a_log_body_limit=2000
```

Optional provider secret env vars: `GOOGLE_GENERATIVE_AI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`, `OPENROUTER_API_KEY`

> Use a repository-scoped fine-grained personal access token with minimal
> required permissions.

Minimal example:

```bash
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha a2a_port=8010
```

Upgrade an existing instance after shared-code update:

```bash
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha update_a2a=true force_restart=true
```

### Provider Configuration Examples

Gemini (Google):

```bash
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" GOOGLE_GENERATIVE_AI_API_KEY="${GOOGLE_GENERATIVE_AI_API_KEY}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha codex_provider_id=google codex_model_id=gemini-3-flash-preview
```

OpenAI:

```bash
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" OPENAI_API_KEY="${OPENAI_API_KEY}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha codex_provider_id=openai codex_model_id='<openai-model-id>'
```

Anthropic:

```bash
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha codex_provider_id=anthropic codex_model_id='<anthropic-model-id>'
```

Azure OpenAI:

```bash
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" AZURE_OPENAI_API_KEY="${AZURE_OPENAI_API_KEY}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha codex_provider_id=azure codex_model_id='<azure-deployment-or-model-id>'
```

OpenRouter:

```bash
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" OPENROUTER_API_KEY="${OPENROUTER_API_KEY}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha codex_provider_id=openrouter codex_model_id='<openrouter-model-id>'
```

Notes:

- Use model IDs that your Codex installation/provider mapping supports.
- This deploy layer mainly passes through provider identity/model (`CODEX_PROVIDER_ID`/`CODEX_MODEL_ID`) and selected provider keys.
- Provider-specific connection settings beyond API key (for example endpoint/base URL, api-version, deployment name) must follow Codex's own provider configuration rules.

### Current Provider Coverage and Gaps

This section describes what this repository's deploy scripts currently cover.
It is not a full Codex provider capability matrix.

| Provider | Secret key persisted by deploy scripts | Example in this doc | Startup key enforcement in `run_codex.sh` |
| --- | --- | --- | --- |
| Google / Gemini | `GOOGLE_GENERATIVE_AI_API_KEY` | Yes | Yes (explicitly required for `provider=google` or `model=*gemini*`) |
| OpenAI | `OPENAI_API_KEY` | Yes | No explicit provider-specific check |
| Anthropic | `ANTHROPIC_API_KEY` | Yes | No explicit provider-specific check |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` | Yes | No explicit provider-specific check |
| OpenRouter | `OPENROUTER_API_KEY` | Yes | No explicit provider-specific check |

Known gaps:

- Missing provider-specific validation matrix in scripts (required env vars are only enforced for Google/Gemini).
- Missing compatibility verification checklist per provider/model family.
- Missing explicit documentation that deploy scripts do not replace Codex `/connect`-level provider setup.

Script actions:

1. install systemd template units `codex@.service` and
   `codex-a2a@.service`
2. create project user and directories
3. write instance config env files
4. start both services (or restart if `force_restart=true`)

## Configuration Details

### `deploy.sh` Inputs and Generated Variables

For values that support both environment variables and CLI keys, precedence is:
`CLI key=value` > environment variable > built-in default.

Naming rule in the tables below:
- `ENV Name` is the process environment variable name.
- `CLI Key` is the `key=value` argument accepted by `deploy.sh`.

#### Secret Variables

| ENV Name | Required | Default | CLI Support | Notes |
| --- | --- | --- | --- | --- |
| `GH_TOKEN` | Conditionally | None | No | Used by Codex and optional `gh auth login`; persisted only when `ENABLE_SECRET_PERSISTENCE=true`. |
| `A2A_BEARER_TOKEN` | Conditionally | None | No | Required by A2A runtime; persisted only when `ENABLE_SECRET_PERSISTENCE=true`. |
| `GOOGLE_GENERATIVE_AI_API_KEY` | Optional | None | No | Persisted into `codex.secret.env` only when `ENABLE_SECRET_PERSISTENCE=true`. |
| `OPENAI_API_KEY` | Optional | None | No | Persisted into `codex.secret.env` only when `ENABLE_SECRET_PERSISTENCE=true`. |
| `ANTHROPIC_API_KEY` | Optional | None | No | Persisted into `codex.secret.env` only when `ENABLE_SECRET_PERSISTENCE=true`. |
| `AZURE_OPENAI_API_KEY` | Optional | None | No | Persisted into `codex.secret.env` only when `ENABLE_SECRET_PERSISTENCE=true`. |
| `OPENROUTER_API_KEY` | Optional | None | No | Persisted into `codex.secret.env` only when `ENABLE_SECRET_PERSISTENCE=true`. |

#### Non-Secret Input Variables

| ENV Name | CLI Key | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `CODEX_A2A_DIR` | - | Optional | `/opt/codex-a2a/codex-a2a-server` | Repo path for codex-a2a-server. |
| `CODEX_CORE_DIR` | - | Optional | `/opt/.codex` | Codex core path. |
| `UV_PYTHON_DIR` | - | Optional | `/opt/uv-python` | uv Python cache path. |
| `DATA_ROOT` | `data_root` | Optional | `/data/codex-a2a` | Instance root directory. |
| `CODEX_BIND_HOST` | - | Optional | `127.0.0.1` | Codex bind host. |
| `CODEX_BIND_PORT` | - | Optional | `A2A_PORT + 1` fallback to `4096` | Multi-instance should use unique port. |
| `CODEX_LOG_LEVEL` | - | Optional | `DEBUG` | Codex log level. |
| `CODEX_EXTRA_ARGS` | - | Optional | empty | Extra Codex startup args. |
| `CODEX_PROVIDER_ID` | `codex_provider_id` | Optional | None | Written to `a2a.env`. |
| `CODEX_MODEL_ID` | `codex_model_id` | Optional | None | Written to `a2a.env`. |
| `CODEX_TIMEOUT` | `codex_timeout` | Optional | `300` | Codex request timeout (seconds). |
| `CODEX_TIMEOUT_STREAM` | `codex_timeout_stream` | Optional | None | Codex streaming timeout (seconds). |
| `GIT_IDENTITY_NAME` | `git_identity_name` | Optional | `Codex-<project>` | Git author/committer name. |
| `GIT_IDENTITY_EMAIL` | `git_identity_email` | Optional | `<project>@example.com` | Git author/committer email. |
| `ENABLE_SECRET_PERSISTENCE` | `enable_secret_persistence` | Optional | `false` | Explicitly allow deploy to write root-only secret env files. |
| `A2A_HOST` | `a2a_host` | Optional | `127.0.0.1` | A2A bind host. |
| `A2A_PORT` | `a2a_port` | Optional | `8000` | A2A bind port. |
| `A2A_PUBLIC_URL` | `a2a_public_url` | Optional | `http://<A2A_HOST>:<A2A_PORT>` | Public Agent Card URL. |
| `A2A_STREAMING` | `a2a_streaming` | Optional | `true` | SSE streaming switch. |
| `A2A_LOG_LEVEL` | `a2a_log_level` | Optional | `DEBUG` | A2A log level. |
| `A2A_LOG_PAYLOADS` | `a2a_log_payloads` | Optional | `false` | Payload logging switch. |
| `A2A_LOG_BODY_LIMIT` | `a2a_log_body_limit` | Optional | `0` | Payload body max length. |

#### Auto-Generated Runtime Variables (Not `deploy.sh` Input ENV)

| Generated Name | Source | Where Written | Notes |
| --- | --- | --- | --- |
| `A2A_PROJECT` | derived from `project=<name>` | `config/a2a.env` | Generated by `setup_instance.sh`; external env injection is not used in deploy flow. |

> Shared paths (`CODEX_A2A_DIR`, `CODEX_CORE_DIR`, `UV_PYTHON_DIR`,
> `DATA_ROOT`) default to `init_system.sh` constants; environment overrides are
> still supported.

> `DATA_ROOT` must be traversable by project users (at least `o+x`). Otherwise
> Codex cannot write `$HOME/.cache` / `$HOME/.local` and `/session` may fail
> with `EACCES`.

### Instance Config Files

For each project (`/data/codex-a2a/<project>/config/`):

- `codex.env`: Codex-only non-secret settings (bind host/port, git identity, askpass path, etc.)
- `codex.auth.env`: root-only runtime secret file for `GH_TOKEN`
- `codex.secret.env`: optional sensitive Codex provider settings
  (`GOOGLE_GENERATIVE_AI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`, `OPENROUTER_API_KEY`)
- `a2a.env`: A2A-only non-secret settings (host/port, logging, model options, etc.)
- `a2a.secret.env`: root-only runtime secret file for `A2A_BEARER_TOKEN`
- `*.example`: root-only templates generated by deploy for secret file provisioning

When `ENABLE_SECRET_PERSISTENCE=true`, deploy writes these secret files as
`600 root:root` and systemd loads them via `EnvironmentFile`. When the flag is
not enabled, operators are expected to provision the real secret files
themselves from the generated templates.

### Token and Key Risk

Because provider keys are injected into the running `codex` process, `codex agent` behavior may indirectly exfiltrate sensitive values.

This architecture does not provide hard guarantees that provider keys are inaccessible to agents. Treat it as a trusted-environment setup unless stronger credential-isolation controls are added.

### Recommended Secret Input Pattern

Use shell prompts or an external secret manager to avoid typing raw secrets into
shell history.

Recommended capture pattern for the opt-in persistence path:

```bash
read -rsp 'GH_TOKEN: ' GH_TOKEN; echo
read -rsp 'A2A_BEARER_TOKEN: ' A2A_BEARER_TOKEN; echo
read -rsp 'GOOGLE_GENERATIVE_AI_API_KEY: ' GOOGLE_GENERATIVE_AI_API_KEY; echo
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" GOOGLE_GENERATIVE_AI_API_KEY="${GOOGLE_GENERATIVE_AI_API_KEY}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh \
  project=alpha \
  a2a_port=8010 \
  a2a_host=127.0.0.1 \
  codex_provider_id=google \
  codex_model_id=gemini-3-flash-preview \
  repo_url=https://github.com/org/repo.git \
  repo_branch=main
```

Rotate Gemini key:

```bash
read -rsp 'GH_TOKEN: ' GH_TOKEN; echo
read -rsp 'A2A_BEARER_TOKEN: ' A2A_BEARER_TOKEN; echo
read -rsp 'GOOGLE_GENERATIVE_AI_API_KEY: ' GOOGLE_GENERATIVE_AI_API_KEY; echo
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" GOOGLE_GENERATIVE_AI_API_KEY="${GOOGLE_GENERATIVE_AI_API_KEY}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha force_restart=true
```

If `repo_url` is provided, first deploy can auto-clone into `workspace/` (optional `repo_branch`). Clone is skipped if `workspace/.git` already exists or workspace is non-empty.

If you manually update env files, restart services:

```bash
sudo systemctl restart codex@<project>.service
sudo systemctl restart codex-a2a@<project>.service
```

### Gemini Key Acceptance Checklist

- first deploy: `config/codex.secret.env` exists with `600` and `root:root` when persistence is enabled
- service restart: Gemini requests still succeed
- host reboot: service auto-recovers and Gemini requests still succeed
- key rotation: new key takes effect after re-running deploy

## Service Management

```bash
sudo systemctl status codex@<project>.service
sudo systemctl status codex-a2a@<project>.service
```

## Uninstall One Instance

To remove a single project instance (services, project dirs, user/group):

```bash
./scripts/uninstall.sh project=<project>
```

By default it prints preview commands only. Apply requires explicit confirmation:

```bash
./scripts/uninstall.sh project=<project> confirm=UNINSTALL
```

Notes:

- `uninstall.sh` never removes shared systemd templates
  (`/etc/systemd/system/codex@.service`,
  `/etc/systemd/system/codex-a2a@.service`).
- It only cleans per-project instance units and resources.
- In apply mode, script validates project name, checks marker env files under
  `${DATA_ROOT}/<project>/config/`, canonicalizes `DATA_ROOT`, and rejects
  unsafe paths containing `.` / `..` segments.
- Script uses `sudo` and expects non-interactive `sudo -n` availability in
  automation contexts.

## Logs

Recent logs:

```bash
sudo journalctl -u codex@<project>.service -n 200 --no-pager
sudo journalctl -u codex-a2a@<project>.service -n 200 --no-pager
```

Follow logs:

```bash
sudo journalctl -u codex@<project>.service -f
sudo journalctl -u codex-a2a@<project>.service -f
```

Errors only:

```bash
sudo journalctl -u codex@<project>.service -p err --no-pager
```

Filter by time:

```bash
sudo journalctl -u codex@<project>.service --since "2026-01-28 14:40" --no-pager
```

Stop services:

```bash
sudo systemctl stop codex-a2a@<project>.service
sudo systemctl stop codex@<project>.service
```

## Security and Isolation

Enabled in systemd units:

- `ProtectSystem=strict`: root filesystem read-only
- `ReadWritePaths=${DATA_ROOT}/%i`: write access scoped to current instance
- `PrivateTmp=true`: private `/tmp`
- `NoNewPrivileges=true`: no privilege escalation for process tree

Application-level safeguards:

- directory boundary validation with `realpath`
- session ownership checks by identity
- credential separation:
  - `A2A_BEARER_TOKEN` only in A2A process
  - `GH_TOKEN` and git credentials only in Codex process

## Streaming Notes

- A2A supports `POST /v1/message:stream` (SSE) when `A2A_STREAMING=true`
- disconnected clients can re-subscribe via
  `GET /v1/tasks/{task_id}:subscribe`
- service subscribes to Codex `/event` stream and forwards filtered
  per-session updates
- stream emits incremental `TaskArtifactUpdateEvent` on a single artifact
  with `codex.block_type` metadata
  (`text` / `reasoning` / `tool_call`) and monotonic `codex.sequence`
- routing is schema-first via Codex `part.type` + `part_id` state, not
  inline marker parsing
- `message.part.delta` may arrive before `message.part.updated`; the service
  buffers those deltas and replays them when the part state is available
- `text` and `reasoning` stream chunks use `TextPart`; `tool_call` stream
  chunks use `DataPart` with a normalized tool payload: `kind=state` carries
  structured state fields such as `tool`, `call_id`, `status`, `input`,
  `output`, and `error`, while `kind=output_delta` carries raw tool text in
  `output_delta` and may also include `source_method`, `tool`, `call_id`, and
  `status`; `item/started` / `item/completed` normalize to `kind=state`,
  `item/*/outputDelta` normalizes to `kind=output_delta`, and legacy
  stringified JSON tool payloads are rejected
- interrupt lifecycle is explicit in `metadata.shared.interrupt`:
  asked events use `phase=asked`, resolved events use `phase=resolved`, and
  resolved events may include `resolution=replied|rejected`
- events without `message_id` are discarded to avoid ambiguous correlation
- final snapshot is emitted only when stream chunks did not already produce
  the same final text; stream then closes with `TaskStatusUpdateEvent(final=true)`
