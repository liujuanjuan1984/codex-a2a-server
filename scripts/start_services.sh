#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

A2A_PORT="${A2A_PORT:-8000}"
A2A_HOST="${A2A_HOST:-127.0.0.1}"
CODEX_LOG_LEVEL="${CODEX_LOG_LEVEL:-DEBUG}"
A2A_LOG_LEVEL="${A2A_LOG_LEVEL:-DEBUG}"
LOG_ROOT="${LOG_ROOT:-${ROOT_DIR}/logs}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="${LOG_DIR:-${LOG_ROOT}/${TIMESTAMP}}"
mkdir -p "$LOG_DIR"
CODEX_LOG="${CODEX_LOG:-${LOG_DIR}/codex_serve.log}"
A2A_LOG="${A2A_LOG:-${LOG_DIR}/codex_a2a.log}"
A2A_PUBLIC_URL="${A2A_PUBLIC_URL:-http://${A2A_HOST}:${A2A_PORT}}"

kill_existing() {
  local pattern="$1"
  local label="$2"
  local pids=""

  if pids="$(pgrep -f "$pattern" || true)"; then
    if [[ -n "$pids" ]]; then
      echo "Stopping existing ${label} (pids: ${pids})..."
      kill ${pids} >/dev/null 2>&1 || true
      for _ in $(seq 1 30); do
        if ! pgrep -f "$pattern" >/dev/null 2>&1; then
          return 0
        fi
        sleep 0.2
      done
      echo "Force killing ${label} (pids: ${pids})..."
      kill -9 ${pids} >/dev/null 2>&1 || true
    fi
  fi
}

CODEX_CMD=""
if command -v codex >/dev/null 2>&1; then
  CODEX_CMD="codex"
elif [[ -x "$HOME/.codex/bin/codex" ]]; then
  CODEX_CMD="$HOME/.codex/bin/codex"
fi

if [[ -z "$CODEX_CMD" ]]; then
  echo "codex binary not found; install it first" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found in PATH" >&2
  exit 1
fi

kill_existing "${CODEX_CMD} serve" "codex serve"
kill_existing "uv run codex-a2a-server" "codex-a2a-server"

echo "Starting codex serve..."
"$CODEX_CMD" serve --log-level "$CODEX_LOG_LEVEL" --print-logs >"$CODEX_LOG" 2>&1 &
CODEX_PID=$!
echo "codex serve pid: ${CODEX_PID} (log: $CODEX_LOG)"

echo "Starting A2A server on ${A2A_HOST}:${A2A_PORT}..."
A2A_HOST="$A2A_HOST" \
A2A_PUBLIC_URL="$A2A_PUBLIC_URL" \
A2A_LOG_LEVEL="$A2A_LOG_LEVEL" \
uv run codex-a2a-server --log-level "$A2A_LOG_LEVEL" >"$A2A_LOG" 2>&1 &
A2A_PID=$!
echo "codex-a2a-server pid: ${A2A_PID} (log: $A2A_LOG)"

cleanup() {
  echo "Stopping services..."
  if [[ -n "${A2A_PID:-}" ]]; then
    kill "${A2A_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${CODEX_PID:-}" ]]; then
    kill "${CODEX_PID}" >/dev/null 2>&1 || true
  fi
  wait "${A2A_PID}" >/dev/null 2>&1 || true
  wait "${CODEX_PID}" >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM HUP

cat <<INFO

A2A service endpoints:
- Agent Card: ${A2A_PUBLIC_URL}/.well-known/agent-card.json
- REST API:   ${A2A_PUBLIC_URL}/v1/message:send
Log directory: ${LOG_DIR}

INFO

echo "Services are running. Press Ctrl+C to stop."
wait -n "${CODEX_PID}" "${A2A_PID}"
echo "One service exited. Shutting down."
