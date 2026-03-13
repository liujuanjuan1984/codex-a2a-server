#!/usr/bin/env bash
# Backward-compatible wrapper retained after codex naming cleanup.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${SCRIPT_DIR}/run_codex.sh"

if [[ ! -x "$TARGET" ]]; then
  echo "run_codex.sh not found at $TARGET" >&2
  exit 1
fi

exec "$TARGET"
