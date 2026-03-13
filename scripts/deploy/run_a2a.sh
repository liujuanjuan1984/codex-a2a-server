#!/usr/bin/env bash
# Wrapper to run codex-a2a-server from the shared venv.
set -euo pipefail

CODEX_A2A_DIR="${CODEX_A2A_DIR:-/opt/codex-a2a/codex-a2a-server}"
A2A_BIN="${A2A_BIN:-${CODEX_A2A_DIR}/.venv/bin/codex-a2a-server}"

if [[ ! -x "$A2A_BIN" ]]; then
  echo "codex-a2a-server entrypoint not found at $A2A_BIN" >&2
  exit 1
fi

if [[ -z "${A2A_BEARER_TOKEN:-}" ]]; then
  echo "A2A_BEARER_TOKEN is required" >&2
  exit 1
fi

exec "$A2A_BIN"
