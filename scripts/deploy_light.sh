#!/usr/bin/env bash
# Lightweight deploy helper for a single local foreground instance.
# No system users, no workspace scaffolding, no permission management.
#
# Usage:
#   A2A_BEARER_TOKEN=<token> ./scripts/deploy_light.sh start workdir=/abs/path [instance=dev] [a2a_host=127.0.0.1] [a2a_port=8000] [a2a_public_url=http://127.0.0.1:8000] [a2a_log_level=INFO] [a2a_streaming=true] [a2a_log_payloads=false] [a2a_log_body_limit=0] [codex_cli_bin=codex] [codex_model=<id>] [codex_model_id=<id>] [codex_model_reasoning_effort=<low|medium|high|xhigh>] [codex_provider_id=<id>] [codex_timeout=120] [codex_timeout_stream=300]
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
CODEX_CLI_BIN="${CODEX_CLI_BIN:-codex}"
CODEX_MODEL="${CODEX_MODEL:-}"
CODEX_MODEL_ID="${CODEX_MODEL_ID:-}"
CODEX_MODEL_REASONING_EFFORT="${CODEX_MODEL_REASONING_EFFORT:-}"
CODEX_PROVIDER_ID="${CODEX_PROVIDER_ID:-}"
CODEX_TIMEOUT="${CODEX_TIMEOUT:-}"
CODEX_TIMEOUT_STREAM="${CODEX_TIMEOUT_STREAM:-}"
A2A_SERVER_BIN=""
LOCAL_CODEX_MODEL=""
LOCAL_CODEX_MODEL_REASONING_EFFORT=""
EFFECTIVE_CODEX_MODEL=""
EFFECTIVE_CODEX_REASONING_EFFORT=""
SERVICE_DEFAULT_CODEX_MODEL="gpt-5.1-codex"

usage() {
  cat <<'USAGE'
Usage:
  A2A_BEARER_TOKEN=<token> ./scripts/deploy_light.sh start workdir=/abs/path [instance=dev] [a2a_host=127.0.0.1] [a2a_port=8000] [a2a_public_url=http://127.0.0.1:8000] [a2a_log_level=INFO] [a2a_streaming=true] [a2a_log_payloads=false] [a2a_log_body_limit=0] [codex_cli_bin=codex] [codex_model=<id>] [codex_model_id=<id>] [codex_model_reasoning_effort=<low|medium|high|xhigh>] [codex_provider_id=<id>] [codex_timeout=120] [codex_timeout_stream=300]

Notes:
  - deploy_light.sh is a foreground launcher.
  - It does not manage stop/status/restart or per-instance pid/log files.
  - Use nohup, pm2, systemd, or another process manager if you need detached
    execution, restart policies, or log capture.
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
    codex_model_reasoning_effort)
      CODEX_MODEL_REASONING_EFFORT="$value"
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

read_local_codex_config_value() {
  local key="$1"
  local config_path="${HOME:-}/.codex/config.toml"
  if [[ -z "${HOME:-}" || ! -f "$config_path" ]]; then
    return 0
  fi

  local in_root=true
  local line
  while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]*\[ ]]; then
      in_root=false
    fi
    if [[ "$in_root" != true ]]; then
      break
    fi
    if [[ "$line" =~ ^[[:space:]]*# || -z "${line//[[:space:]]/}" ]]; then
      continue
    fi
    if [[ "$line" =~ ^[[:space:]]*${key}[[:space:]]*=[[:space:]]*\"([^\"]*)\" ]]; then
      printf '%s\n' "${BASH_REMATCH[1]}"
      return 0
    fi
  done <"$config_path"
}

display_or_unset() {
  local value="$1"
  if [[ -n "$value" ]]; then
    printf '%s\n' "$value"
    return 0
  fi
  printf '<unset>\n'
}

resolve_effective_codex_config() {
  LOCAL_CODEX_MODEL="$(read_local_codex_config_value model)"
  LOCAL_CODEX_MODEL_REASONING_EFFORT="$(read_local_codex_config_value model_reasoning_effort)"

  if [[ -z "$CODEX_MODEL" && -z "$CODEX_MODEL_ID" && -n "$LOCAL_CODEX_MODEL" ]]; then
    CODEX_MODEL="$LOCAL_CODEX_MODEL"
  fi
  if [[ -z "$CODEX_MODEL_REASONING_EFFORT" && -n "$LOCAL_CODEX_MODEL_REASONING_EFFORT" ]]; then
    CODEX_MODEL_REASONING_EFFORT="$LOCAL_CODEX_MODEL_REASONING_EFFORT"
  fi

  if [[ -n "$CODEX_MODEL_ID" ]]; then
    EFFECTIVE_CODEX_MODEL="$CODEX_MODEL_ID"
  elif [[ -n "$CODEX_MODEL" ]]; then
    EFFECTIVE_CODEX_MODEL="$CODEX_MODEL"
  else
    EFFECTIVE_CODEX_MODEL="$SERVICE_DEFAULT_CODEX_MODEL"
  fi

  if [[ -n "$CODEX_MODEL_REASONING_EFFORT" ]]; then
    EFFECTIVE_CODEX_REASONING_EFFORT="$CODEX_MODEL_REASONING_EFFORT"
  else
    EFFECTIVE_CODEX_REASONING_EFFORT=""
  fi
}

resolve_a2a_server_bin() {
  local local_bin="${ROOT_DIR}/.venv/bin/codex-a2a-server"
  if [[ -x "$local_bin" ]]; then
    A2A_SERVER_BIN="$local_bin"
    return 0
  fi

  if command -v codex-a2a-server >/dev/null 2>&1; then
    A2A_SERVER_BIN="$(command -v codex-a2a-server)"
    return 0
  fi

  echo "codex-a2a-server binary not found. Expected ${local_bin} or codex-a2a-server in PATH." >&2
  echo "Run uv sync --all-extras or install the package entrypoint before using deploy_light." >&2
  exit 1
}

print_codex_config_summary() {
  echo "Codex config summary:"
  echo "  local config model: $(display_or_unset "$LOCAL_CODEX_MODEL")"
  echo "  local config reasoning_effort: $(display_or_unset "$LOCAL_CODEX_MODEL_REASONING_EFFORT")"
  echo "  effective instance model: ${EFFECTIVE_CODEX_MODEL}"
  echo "  a2a server bin: ${A2A_SERVER_BIN}"
  if [[ -n "$EFFECTIVE_CODEX_REASONING_EFFORT" ]]; then
    echo "  effective instance reasoning_effort: ${EFFECTIVE_CODEX_REASONING_EFFORT}"
  else
    echo "  effective instance reasoning_effort: <codex-default>"
  fi
}

validate_codex_config() {
  local normalized_model="${EFFECTIVE_CODEX_MODEL,,}"
  local normalized_effort="${EFFECTIVE_CODEX_REASONING_EFFORT,,}"

  if [[ "$normalized_effort" == "xhigh" && "$normalized_model" == gpt-5.1-codex* ]]; then
    echo "Refusing to start '${INSTANCE}': reasoning_effort=xhigh is not supported with model ${EFFECTIVE_CODEX_MODEL}." >&2
    echo "Suggested fixes:" >&2
    echo "  1. Add codex_model_reasoning_effort=high" >&2
    echo "  2. Or switch to codex_model=gpt-5.4" >&2
    exit 1
  fi
}

require_start_prerequisites() {
  if [[ -z "${A2A_BEARER_TOKEN:-}" ]]; then
    echo "A2A_BEARER_TOKEN is required for start." >&2
    exit 1
  fi
  if [[ -z "$WORKDIR" ]]; then
    echo "workdir is required for start." >&2
    exit 1
  fi
  if [[ ! -d "$WORKDIR" ]]; then
    echo "workdir does not exist: $WORKDIR" >&2
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
  resolve_a2a_server_bin
  resolve_effective_codex_config
  print_codex_config_summary
  validate_codex_config
}

start_instance() {
  require_start_prerequisites

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
  export CODEX_A2A_LIGHT_INSTANCE="$INSTANCE"
  export CODEX_A2A_LIGHT_ROOT="$ROOT_DIR"
  if [[ -n "$CODEX_MODEL" ]]; then
    export CODEX_MODEL
  fi
  if [[ -n "$CODEX_MODEL_ID" ]]; then
    export CODEX_MODEL_ID
  fi
  if [[ -n "$CODEX_MODEL_REASONING_EFFORT" ]]; then
    export CODEX_MODEL_REASONING_EFFORT
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

  cat <<INFO
Instance '${INSTANCE}' starting in foreground.
Agent Card: ${A2A_PUBLIC_URL}/.well-known/agent-card.json
REST endpoint: ${A2A_PUBLIC_URL}/v1/message:send
Workdir: ${WORKDIR}

Use nohup, pm2, systemd, or another process manager if you need detached
execution, restart policies, or persistent log capture.
INFO

  exec "$A2A_SERVER_BIN"
}

case "${ACTION,,}" in
  start)
    start_instance
    ;;
  stop|status|restart)
    echo "deploy_light.sh is a foreground launcher and no longer supports ${ACTION,,}." >&2
    echo "Use your process manager to stop/restart the process it launched." >&2
    exit 1
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    usage
    exit 1
    ;;
esac
