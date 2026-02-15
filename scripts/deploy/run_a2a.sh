#!/usr/bin/env bash
# Wrapper to run codex-a2a-serve from the shared venv.
set -euo pipefail

OPENCODE_A2A_DIR="${OPENCODE_A2A_DIR:-/opt/codex-a2a/codex-a2a-serve}"
A2A_BIN="${A2A_BIN:-${OPENCODE_A2A_DIR}/.venv/bin/codex-a2a-serve}"

if [[ ! -x "$A2A_BIN" ]]; then
  echo "codex-a2a-serve entrypoint not found at $A2A_BIN" >&2
  exit 1
fi

if [[ -z "${A2A_BEARER_TOKEN:-}" ]]; then
  echo "A2A_BEARER_TOKEN is required" >&2
  exit 1
fi

exec "$A2A_BIN"
