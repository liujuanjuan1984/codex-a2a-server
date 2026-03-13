#!/usr/bin/env bash
# Update the shared codex-a2a-server environment (no git operations).
# Requires env: CODEX_A2A_DIR.
set -euo pipefail

: "${CODEX_A2A_DIR:?}"

if [[ ! -d "$CODEX_A2A_DIR" ]]; then
  echo "CODEX_A2A_DIR not found: $CODEX_A2A_DIR" >&2
  exit 1
fi

if [[ ! -f "${CODEX_A2A_DIR}/pyproject.toml" ]]; then
  echo "pyproject.toml not found in ${CODEX_A2A_DIR}" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found in PATH; cannot sync venv" >&2
  exit 1
fi

echo "Syncing codex-a2a-server venv in ${CODEX_A2A_DIR}..."
(
  cd "$CODEX_A2A_DIR"
  uv sync --all-extras
)
