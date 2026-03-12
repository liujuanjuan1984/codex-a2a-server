#!/usr/bin/env bash
# Lightweight deploy helper for a single local/background instance.
# No system users, no workspace scaffolding, no permission management.
#
# Usage:
#   A2A_BEARER_TOKEN=<token> ./scripts/deploy_light.sh start workdir=/abs/path [instance=dev] [a2a_host=127.0.0.1] [a2a_port=8000] [a2a_public_url=http://127.0.0.1:8000] [a2a_log_level=INFO] [a2a_streaming=true] [a2a_log_payloads=false] [a2a_log_body_limit=0] [codex_cli_bin=codex] [codex_model=<id>] [codex_model_id=<id>] [codex_provider_id=<id>] [codex_timeout=120] [codex_timeout_stream=300] [log_root=./logs/light] [pid_root=./run/light]
#   ./scripts/deploy_light.sh stop [instance=dev] [pid_root=./run/light]
#   ./scripts/deploy_light.sh status [instance=dev] [pid_root=./run/light]
#   A2A_BEARER_TOKEN=<token> ./scripts/deploy_light.sh restart workdir=/abs/path [instance=dev]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ACTION="${1:-}"
if [[ -n "$ACTION" ]]; then
  shift
fi

INSTANCE="default"
WORKDIR=""
A2A_HOST="127.0.0.1"
A2A_PORT="8000"
A2A_PUBLIC_URL=""
A2A_LOG_LEVEL="INFO"
A2A_STREAMING="true"
A2A_LOG_PAYLOADS="false"
A2A_LOG_BODY_LIMIT="0"
CODEX_CLI_BIN="codex"
CODEX_MODEL=""
CODEX_MODEL_ID=""
CODEX_PROVIDER_ID=""
CODEX_TIMEOUT=""
CODEX_TIMEOUT_STREAM=""
LOG_ROOT="${ROOT_DIR}/logs/light"
PID_ROOT="${ROOT_DIR}/run/light"

usage() {
  cat <<'USAGE'
Usage:
  A2A_BEARER_TOKEN=<token> ./scripts/deploy_light.sh start workdir=/abs/path [instance=dev] [a2a_host=127.0.0.1] [a2a_port=8000] [a2a_public_url=http://127.0.0.1:8000] [a2a_log_level=INFO] [a2a_streaming=true] [a2a_log_payloads=false] [a2a_log_body_limit=0] [codex_cli_bin=codex] [codex_model=<id>] [codex_model_id=<id>] [codex_provider_id=<id>] [codex_timeout=120] [codex_timeout_stream=300] [log_root=./logs/light] [pid_root=./run/light]
  ./scripts/deploy_light.sh stop [instance=dev] [pid_root=./run/light]
  ./scripts/deploy_light.sh status [instance=dev] [pid_root=./run/light]
  A2A_BEARER_TOKEN=<token> ./scripts/deploy_light.sh restart workdir=/abs/path [instance=dev]
USAGE
}

for arg in "$@"; do
  if [[ "$arg" != *=* ]]; then
    echo "Unknown argument format: $arg (expected key=value)" >&2
    usage
    exit 1
  fi
  key="${arg%%=*}"
  value="${arg#*=}"
  case "${key,,}" in
    instance)
      INSTANCE="$value"
      ;;
    workdir)
      WORKDIR="$value"
      ;;
    a2a_host)
      A2A_HOST="$value"
      ;;
    a2a_port)
      A2A_PORT="$value"
      ;;
    a2a_public_url)
      A2A_PUBLIC_URL="$value"
      ;;
    a2a_log_level)
      A2A_LOG_LEVEL="$value"
      ;;
    a2a_streaming)
      A2A_STREAMING="$value"
      ;;
    a2a_log_payloads)
      A2A_LOG_PAYLOADS="$value"
      ;;
    a2a_log_body_limit)
      A2A_LOG_BODY_LIMIT="$value"
      ;;
    codex_cli_bin)
      CODEX_CLI_BIN="$value"
      ;;
    codex_model)
      CODEX_MODEL="$value"
      ;;
    codex_model_id)
      CODEX_MODEL_ID="$value"
      ;;
    codex_provider_id)
      CODEX_PROVIDER_ID="$value"
      ;;
    codex_timeout)
      CODEX_TIMEOUT="$value"
      ;;
    codex_timeout_stream)
      CODEX_TIMEOUT_STREAM="$value"
      ;;
    log_root)
      LOG_ROOT="$value"
      ;;
    pid_root)
      PID_ROOT="$value"
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$ACTION" ]]; then
  usage
  exit 1
fi

if [[ -z "$A2A_PUBLIC_URL" ]]; then
  A2A_PUBLIC_URL="http://${A2A_HOST}:${A2A_PORT}"
fi

PID_FILE="${PID_ROOT}/${INSTANCE}.pid"
LOG_FILE="${LOG_ROOT}/${INSTANCE}.log"

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  kill -0 "$pid" >/dev/null 2>&1
}

require_start_prerequisites() {
  if [[ -z "${A2A_BEARER_TOKEN:-}" ]]; then
    echo "A2A_BEARER_TOKEN is required for start/restart." >&2
    exit 1
  fi
  if [[ -z "$WORKDIR" ]]; then
    echo "workdir is required for start/restart." >&2
    exit 1
  fi
  if [[ ! -d "$WORKDIR" ]]; then
    echo "workdir does not exist: $WORKDIR" >&2
    exit 1
  fi
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found in PATH." >&2
    exit 1
  fi
  if [[ "$CODEX_CLI_BIN" == */* ]]; then
    if [[ ! -x "$CODEX_CLI_BIN" ]]; then
      echo "codex binary not executable: $CODEX_CLI_BIN" >&2
      exit 1
    fi
  elif ! command -v "$CODEX_CLI_BIN" >/dev/null 2>&1; then
    echo "codex binary not found in PATH: $CODEX_CLI_BIN" >&2
    exit 1
  fi
}

start_instance() {
  require_start_prerequisites
  mkdir -p "$PID_ROOT" "$LOG_ROOT"
  if is_running; then
    echo "Instance '${INSTANCE}' is already running (pid=$(cat "$PID_FILE"))."
    exit 0
  fi

  (
    export A2A_HOST
    export A2A_PORT
    export A2A_PUBLIC_URL
    export A2A_LOG_LEVEL
    export A2A_STREAMING
    export A2A_LOG_PAYLOADS
    export A2A_LOG_BODY_LIMIT
    export A2A_BEARER_TOKEN
    export CODEX_CLI_BIN
    export CODEX_DIRECTORY="$WORKDIR"
    if [[ -n "$CODEX_MODEL" ]]; then
      export CODEX_MODEL
    fi
    if [[ -n "$CODEX_MODEL_ID" ]]; then
      export CODEX_MODEL_ID
    fi
    if [[ -n "$CODEX_PROVIDER_ID" ]]; then
      export CODEX_PROVIDER_ID
    fi
    if [[ -n "$CODEX_TIMEOUT" ]]; then
      export CODEX_TIMEOUT
    fi
    if [[ -n "$CODEX_TIMEOUT_STREAM" ]]; then
      export CODEX_TIMEOUT_STREAM
    fi
    exec uv run codex-a2a-serve
  ) >>"$LOG_FILE" 2>&1 &

  local pid="$!"
  echo "$pid" >"$PID_FILE"
  sleep 1
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    echo "Failed to start instance '${INSTANCE}'. Check log: $LOG_FILE" >&2
    rm -f "$PID_FILE"
    exit 1
  fi

  cat <<INFO
Instance '${INSTANCE}' started.
PID: ${pid}
Log: ${LOG_FILE}
Agent Card: ${A2A_PUBLIC_URL}/.well-known/agent-card.json
REST endpoint: ${A2A_PUBLIC_URL}/v1/message:send
Workdir: ${WORKDIR}
INFO
}

stop_instance() {
  if ! is_running; then
    rm -f "$PID_FILE"
    echo "Instance '${INSTANCE}' is not running."
    exit 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      rm -f "$PID_FILE"
      echo "Instance '${INSTANCE}' stopped."
      exit 0
    fi
    sleep 0.2
  done
  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$PID_FILE"
  echo "Instance '${INSTANCE}' force-stopped."
}

status_instance() {
  if is_running; then
    echo "Instance '${INSTANCE}' is running (pid=$(cat "$PID_FILE"))."
    echo "Log: $LOG_FILE"
    exit 0
  fi
  echo "Instance '${INSTANCE}' is not running."
  exit 1
}

case "${ACTION,,}" in
  start)
    start_instance
    ;;
  stop)
    stop_instance
    ;;
  restart)
    stop_instance || true
    start_instance
    ;;
  status)
    status_instance
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    usage
    exit 1
    ;;
esac
