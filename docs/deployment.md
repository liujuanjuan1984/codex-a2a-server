# Deployment Guide (systemd Multi-Instance)

This guide covers the managed deployment path for `codex-a2a-server`.

Deployment is intentionally split into three layers:

- development: run from a source checkout with `uv run codex-a2a-server`
- self-start: install the published CLI with `uv tool install codex-a2a-server`
- managed deployment: install the published package into a shared runtime and
  run per-project `codex-a2a@.service` instances with systemd

This document only covers the third layer.

## Model

Managed deployment now uses a single long-running service per project:

- service unit: `codex-a2a@<project>.service`
- runtime: published `codex-a2a-server` package installed into a shared virtual
  environment
- Codex integration: the service starts the Codex app-server subprocess itself
  through `CODEX_APP_SERVER_LISTEN=stdio://`

The managed path no longer requires:

- a checkout of this repository on the target host
- a repository-local `.venv`
- a second systemd service for Codex itself

## Prerequisites

- `sudo` access
- systemd
- Codex installed in a shared location
  (default `/opt/.codex`; configured in `scripts/init_system.sh`)
- `uv`
- network access to PyPI unless you override the package source

## Bootstrap Host Prerequisites

Prepare the shared runtime once:

```bash
./scripts/init_system.sh
```

Default bootstrap behavior:

- installs base tools, `gh`, Node.js, and `uv`
- pre-downloads Python `3.10/3.11/3.12/3.13`
- creates shared directories under `/opt/codex-a2a`, `/opt/.codex`,
  `/opt/uv-python`, and `/data/codex-a2a`
- creates `/opt/codex-a2a/runtime`
- installs the published `codex-a2a-server` package into that runtime

Important defaults from `scripts/init_system.sh`:

- `CODEX_A2A_ROOT=/opt/codex-a2a`
- `CODEX_A2A_RUNTIME_DIR=/opt/codex-a2a/runtime`
- `CODEX_A2A_PACKAGE_SPEC=codex-a2a-server`
- `CODEX_A2A_PYTHON_VERSION=3.13`

If you need a different released version or index source, edit the constants in
`scripts/init_system.sh` before running it.

## Instance Layout

Each managed instance lives under `${DATA_ROOT}/<project>`
(`DATA_ROOT=/data/codex-a2a` by default):

- `workspace/`: Codex working directory for that project
- `config/`: root-only environment files
- `logs/`: reserved for operator-managed logs
- `run/`: reserved for runtime helper files

Default permissions:

- `DATA_ROOT`: `711`
- project root + `workspace` + `logs` + `run`: `700`
- `config/`: `700`
- config env files: `600`

## Deploy One Instance

Recommended secure workflow:

1. Bootstrap directories and config templates.

```bash
codex-a2a-server deploy --project alpha --a2a-host 127.0.0.1 --a2a-port 8010
```

2. Populate the generated root-only secret files.

```bash
sudo cp /data/codex-a2a/alpha/config/a2a.secret.env.example /data/codex-a2a/alpha/config/a2a.secret.env
sudoedit /data/codex-a2a/alpha/config/a2a.secret.env
```

3. Re-run deploy to enable the service.

```bash
codex-a2a-server deploy --project alpha --a2a-host 127.0.0.1 --a2a-port 8010
```

One-step deploy with secret persistence enabled:

```bash
read -rsp 'A2A_BEARER_TOKEN: ' A2A_BEARER_TOKEN; echo
A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" ENABLE_SECRET_PERSISTENCE=true \
codex-a2a-server deploy --project alpha --a2a-host 127.0.0.1 --a2a-port 8010
```

Public URL example:

```bash
A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" ENABLE_SECRET_PERSISTENCE=true \
codex-a2a-server deploy \
  --project alpha \
  --a2a-host 127.0.0.1 \
  --a2a-port 8010 \
  --a2a-public-url https://a2a.example.com
```

## Runtime Secret Files

By default `ENABLE_SECRET_PERSISTENCE=false`, so deploy does not write secrets
to disk. It expects these files:

- `config/a2a.secret.env`: required, contains `A2A_BEARER_TOKEN`
- `config/codex.secret.env`: optional provider keys such as
  `OPENAI_API_KEY` or `GOOGLE_GENERATIVE_AI_API_KEY`

Templates are generated automatically as:

- `a2a.secret.env.example`
- `codex.secret.env.example`

The released CLI uses flags as the preferred contract. For compatibility, the
underlying packaged deploy helper still accepts legacy `key=value` passthrough.

## Supported `codex-a2a-server deploy` Inputs

Common CLI keys:

- `--project`
- `--data-root`
- `--a2a-host`
- `--a2a-port`
- `--a2a-public-url`
- `--a2a-enable-health-endpoint` / `--no-a2a-enable-health-endpoint`
- `--a2a-enable-session-shell` / `--no-a2a-enable-session-shell`
- `--a2a-interrupt-request-ttl-seconds`
- `--a2a-log-level`
- `--a2a-log-payloads` / `--no-a2a-log-payloads`
- `--a2a-log-body-limit`
- `--codex-provider-id`
- `--codex-model-id`
- `--codex-timeout`
- `--codex-timeout-stream`
- `--package-spec`
- `--git-identity-name`
- `--git-identity-email`
- `--enable-secret-persistence` / `--no-enable-secret-persistence`
- `--update-a2a` / `--no-update-a2a`
- `--force-restart` / `--no-force-restart`

Optional workspace bootstrap keys:

Notes:
- `--package-spec` controls which published package spec is installed into the
  shared runtime when `update_a2a=true`.
- runtime install precedence is `--package-spec <spec>` CLI override, then
  `CODEX_A2A_PACKAGE_SPEC`, then the default `codex-a2a-server`.

## Upgrade the Shared Runtime

To upgrade the shared managed runtime to the latest published version:

```bash
codex-a2a-server deploy --project alpha --update-a2a --force-restart
```

To pin a specific release:

```bash
codex-a2a-server deploy \
  --project alpha \
  --package-spec 'codex-a2a-server==0.1.0' \
  --update-a2a \
  --force-restart
```

This refreshes `/opt/codex-a2a/runtime` and restarts
`codex-a2a@alpha.service`.

## Generated Config Files

Per instance, deploy writes:

- `config/codex.env`: non-secret Codex settings
  - `CODEX_APP_SERVER_LISTEN`
  - `CODEX_DIRECTORY`
  - `CODEX_TIMEOUT`
  - `CODEX_TIMEOUT_STREAM`
  - `CODEX_PROVIDER_ID`
  - `CODEX_MODEL_ID`
  - git author identity settings
- `config/codex.secret.env`: root-only provider keys
- `config/a2a.env`: non-secret A2A settings
  - `A2A_ENABLE_HEALTH_ENDPOINT`
  - `A2A_ENABLE_SESSION_SHELL`
  - `A2A_INTERRUPT_REQUEST_TTL_SECONDS`
- `config/a2a.secret.env`: root-only `A2A_BEARER_TOKEN`

The systemd unit loads both `codex.env` and `a2a.env`, so Codex subprocess
settings and A2A settings stay in one service boundary.

## Deploy Readiness Probe

When `A2A_ENABLE_HEALTH_ENDPOINT=true`, `scripts/deploy/enable_instance.sh`
waits for an authenticated `GET /health` probe before considering the service
ready.

- The probe uses `Authorization: Bearer <token>`
- It prefers `A2A_BEARER_TOKEN` from the current environment
- Otherwise it reads `${DATA_ROOT}/<project>/config/a2a.secret.env`
- If `A2A_ENABLE_HEALTH_ENDPOINT=false`, deploy skips the `/health` probe and
  only verifies systemd service state

Optional deploy-only tuning:

- `DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS`: readiness timeout, default `30`
- `DEPLOY_HEALTHCHECK_INTERVAL_SECONDS`: probe interval, default `1`

## Service Management

Inspect the deployed service:

```bash
sudo systemctl status codex-a2a@alpha.service --no-pager
```

Restart it:

```bash
sudo systemctl restart codex-a2a@alpha.service
```

Tail logs:

```bash
sudo journalctl -u codex-a2a@alpha.service -f
```

Show recent errors:

```bash
sudo journalctl -u codex-a2a@alpha.service -p err --no-pager
```

## Uninstall

Preview uninstall actions:

```bash
./scripts/uninstall.sh project=alpha
```

Apply uninstall:

```bash
./scripts/uninstall.sh project=alpha confirm=UNINSTALL
```

The uninstall flow removes the per-project instance and user. It does not
remove the shared runtime or the shared template unit.
