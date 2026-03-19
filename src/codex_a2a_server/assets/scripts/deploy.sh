#!/usr/bin/env bash
# Deploy an isolated Codex + A2A instance (single systemd service per project).
# Usage: ./deploy.sh project=<name> [data_root=<path>] [a2a_port=<port>] [a2a_host=<host>] [a2a_public_url=<url>] [a2a_enable_health_endpoint=<bool>] [a2a_enable_session_shell=<bool>] [a2a_interrupt_request_ttl_seconds=<int>] [a2a_log_level=<level>] [a2a_log_payloads=<bool>] [a2a_log_body_limit=<int>] [codex_provider_id=<id>] [codex_model_id=<id>] [repo_url=<url>] [repo_branch=<branch>] [package_spec=<spec>] [codex_timeout=<seconds>] [codex_timeout_stream=<seconds>] [git_identity_name=<name>] [git_identity_email=<email>] [enable_secret_persistence=<bool>] [update_a2a=true] [force_restart=true]
# Secret env vars are only required when persisting them during deploy or when setup actions need them.
# Optional provider secret env: see scripts/deploy/provider_secret_env_keys.sh
# Requires: sudo access to write systemd units and create users/directories.
#
# Source of truth for all variable semantics/defaults:
# - docs/deployment.md (section: "deploy.sh Inputs and Generated Variables")
# Non-secret options that support both env and CLI key=value use precedence:
# - CLI > env > default
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/deploy/provider_secret_env_keys.sh"
PROVIDER_SECRET_ENV_LIST="$(join_provider_secret_env_keys " | ")"

PROJECT_NAME=""
A2A_PORT_INPUT=""
A2A_HOST_INPUT=""
A2A_PUBLIC_URL_INPUT=""
A2A_ENABLE_HEALTH_ENDPOINT_INPUT=""
A2A_ENABLE_SESSION_SHELL_INPUT=""
A2A_INTERRUPT_REQUEST_TTL_SECONDS_INPUT=""
A2A_LOG_LEVEL_INPUT=""
A2A_LOG_PAYLOADS_INPUT=""
A2A_LOG_BODY_LIMIT_INPUT=""
DATA_ROOT_INPUT=""
CODEX_PROVIDER_ID_INPUT=""
CODEX_MODEL_ID_INPUT=""
REPO_URL_INPUT=""
REPO_BRANCH_INPUT=""
PACKAGE_SPEC_INPUT=""
CODEX_TIMEOUT_INPUT=""
CODEX_TIMEOUT_STREAM_INPUT=""
GIT_IDENTITY_NAME_INPUT=""
GIT_IDENTITY_EMAIL_INPUT=""
UPDATE_A2A_INPUT=""
FORCE_RESTART_INPUT=""
ENABLE_SECRET_PERSISTENCE_INPUT=""

for arg in "$@"; do
  if [[ "$arg" == *=* ]]; then
    key="${arg%%=*}"
    value="${arg#*=}"
  else
    echo "Unknown argument format: $arg (expected key=value)" >&2
    exit 1
  fi

  case "${key,,}" in
    project|project_name)
      PROJECT_NAME="$value"
      ;;
    github_token|gh_token)
      echo "Sensitive parameter '${key}' is not allowed via CLI. Use environment variable GH_TOKEN." >&2
      exit 1
      ;;
    a2a_bearer_token|bearer_token)
      echo "Sensitive parameter '${key}' is not allowed via CLI. Use environment variable A2A_BEARER_TOKEN." >&2
      exit 1
      ;;
    a2a_port)
      A2A_PORT_INPUT="$value"
      ;;
    data_root)
      DATA_ROOT_INPUT="$value"
      ;;
    a2a_host)
      A2A_HOST_INPUT="$value"
      ;;
    a2a_public_url)
      A2A_PUBLIC_URL_INPUT="$value"
      ;;
    a2a_enable_health_endpoint)
      A2A_ENABLE_HEALTH_ENDPOINT_INPUT="$value"
      ;;
    a2a_enable_session_shell)
      A2A_ENABLE_SESSION_SHELL_INPUT="$value"
      ;;
    a2a_interrupt_request_ttl_seconds)
      A2A_INTERRUPT_REQUEST_TTL_SECONDS_INPUT="$value"
      ;;
    a2a_log_level)
      A2A_LOG_LEVEL_INPUT="$value"
      ;;
    a2a_log_payloads)
      A2A_LOG_PAYLOADS_INPUT="$value"
      ;;
    a2a_log_body_limit)
      A2A_LOG_BODY_LIMIT_INPUT="$value"
      ;;
    codex_provider_id)
      CODEX_PROVIDER_ID_INPUT="$value"
      ;;
    codex_model_id)
      CODEX_MODEL_ID_INPUT="$value"
      ;;
    repo_url)
      REPO_URL_INPUT="$value"
      ;;
    repo_branch)
      REPO_BRANCH_INPUT="$value"
      ;;
    package_spec)
      PACKAGE_SPEC_INPUT="$value"
      ;;
    codex_timeout)
      CODEX_TIMEOUT_INPUT="$value"
      ;;
    codex_timeout_stream)
      CODEX_TIMEOUT_STREAM_INPUT="$value"
      ;;
    git_identity_name)
      GIT_IDENTITY_NAME_INPUT="$value"
      ;;
    git_identity_email)
      GIT_IDENTITY_EMAIL_INPUT="$value"
      ;;
    enable_secret_persistence)
      ENABLE_SECRET_PERSISTENCE_INPUT="$value"
      ;;
    update_a2a)
      UPDATE_A2A_INPUT="$value"
      ;;
    force_restart)
      FORCE_RESTART_INPUT="$value"
      ;;
    *)
      if provider_env_key="$(provider_secret_env_for_cli_key "${key,,}" 2>/dev/null)"; then
        echo "Sensitive parameter '${key}' is not allowed via CLI. Use environment variable ${provider_env_key}." >&2
        exit 1
      fi
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$PROJECT_NAME" ]]; then
  cat >&2 <<USAGE
Usage:
  [GH_TOKEN=<token>] [A2A_BEARER_TOKEN=<token>] [<PROVIDER_SECRET_ENV>=<key>] \
  ./scripts/deploy.sh project=<name> [data_root=<path>] [a2a_port=<port>] [a2a_host=<host>] [a2a_public_url=<url>] \
  [a2a_enable_health_endpoint=<bool>] [a2a_enable_session_shell=<bool>] \
  [a2a_interrupt_request_ttl_seconds=<int>] [a2a_log_level=<level>] [a2a_log_payloads=<bool>] [a2a_log_body_limit=<int>] \
  [codex_provider_id=<id>] [codex_model_id=<id>] [repo_url=<url>] [repo_branch=<branch>] [package_spec=<spec>] \
  [codex_timeout=<seconds>] [codex_timeout_stream=<seconds>] [git_identity_name=<name>] [enable_secret_persistence=<bool>] \
  [git_identity_email=<email>] [update_a2a=true] [force_restart=true]

Provider secret env vars:
  ${PROVIDER_SECRET_ENV_LIST}
USAGE
  exit 1
fi

export CODEX_A2A_ROOT="${CODEX_A2A_ROOT:-/opt/codex-a2a}"
export CODEX_A2A_RUNTIME_DIR="${CODEX_A2A_RUNTIME_DIR:-${CODEX_A2A_ROOT}/runtime}"
export CODEX_A2A_PACKAGE_SPEC="${CODEX_A2A_PACKAGE_SPEC:-codex-a2a-server}"
export CODEX_A2A_PYTHON_VERSION="${CODEX_A2A_PYTHON_VERSION:-3.13}"
export CODEX_CORE_DIR="${CODEX_CORE_DIR:-/opt/.codex}"
export UV_PYTHON_DIR="${UV_PYTHON_DIR:-/opt/uv-python}"
export DATA_ROOT="${DATA_ROOT:-/data/codex-a2a}"

export_if_present() {
  local target="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    export "${target}=${value}"
  fi
}

export_if_present "CODEX_PROVIDER_ID" "$CODEX_PROVIDER_ID_INPUT"
export_if_present "CODEX_MODEL_ID" "$CODEX_MODEL_ID_INPUT"
export_if_present "REPO_URL" "$REPO_URL_INPUT"
export_if_present "REPO_BRANCH" "$REPO_BRANCH_INPUT"
export_if_present "CODEX_A2A_PACKAGE_SPEC" "$PACKAGE_SPEC_INPUT"
export_if_present "CODEX_TIMEOUT" "$CODEX_TIMEOUT_INPUT"
export_if_present "CODEX_TIMEOUT_STREAM" "$CODEX_TIMEOUT_STREAM_INPUT"
export_if_present "GIT_IDENTITY_NAME" "$GIT_IDENTITY_NAME_INPUT"
export_if_present "GIT_IDENTITY_EMAIL" "$GIT_IDENTITY_EMAIL_INPUT"
export_if_present "DATA_ROOT" "$DATA_ROOT_INPUT"

export ENABLE_SECRET_PERSISTENCE="${ENABLE_SECRET_PERSISTENCE:-false}"

if [[ -n "$A2A_HOST_INPUT" ]]; then
  export A2A_HOST="$A2A_HOST_INPUT"
else
  export A2A_HOST="${A2A_HOST:-127.0.0.1}"
fi
if [[ -n "$A2A_PORT_INPUT" ]]; then
  export A2A_PORT="$A2A_PORT_INPUT"
else
  export A2A_PORT="${A2A_PORT:-8000}"
fi
if [[ -n "$A2A_PUBLIC_URL_INPUT" ]]; then
  export A2A_PUBLIC_URL="$A2A_PUBLIC_URL_INPUT"
else
  export A2A_PUBLIC_URL="http://${A2A_HOST}:${A2A_PORT}"
fi

export A2A_LOG_LEVEL="${A2A_LOG_LEVEL:-DEBUG}"
export A2A_ENABLE_HEALTH_ENDPOINT="${A2A_ENABLE_HEALTH_ENDPOINT:-true}"
export A2A_ENABLE_SESSION_SHELL="${A2A_ENABLE_SESSION_SHELL:-true}"
export A2A_INTERRUPT_REQUEST_TTL_SECONDS="${A2A_INTERRUPT_REQUEST_TTL_SECONDS:-3600}"
export A2A_LOG_PAYLOADS="${A2A_LOG_PAYLOADS:-false}"
export A2A_LOG_BODY_LIMIT="${A2A_LOG_BODY_LIMIT:-0}"
export_if_present "A2A_LOG_LEVEL" "$A2A_LOG_LEVEL_INPUT"
export_if_present "A2A_ENABLE_HEALTH_ENDPOINT" "$A2A_ENABLE_HEALTH_ENDPOINT_INPUT"
export_if_present "A2A_ENABLE_SESSION_SHELL" "$A2A_ENABLE_SESSION_SHELL_INPUT"
export_if_present "A2A_INTERRUPT_REQUEST_TTL_SECONDS" "$A2A_INTERRUPT_REQUEST_TTL_SECONDS_INPUT"
export_if_present "A2A_LOG_PAYLOADS" "$A2A_LOG_PAYLOADS_INPUT"
export_if_present "A2A_LOG_BODY_LIMIT" "$A2A_LOG_BODY_LIMIT_INPUT"
export_if_present "ENABLE_SECRET_PERSISTENCE" "$ENABLE_SECRET_PERSISTENCE_INPUT"

is_truthy() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

UPDATE_A2A="false"
FORCE_RESTART="false"
if [[ -n "$UPDATE_A2A_INPUT" ]] && is_truthy "$UPDATE_A2A_INPUT"; then
  UPDATE_A2A="true"
fi
if [[ -n "$FORCE_RESTART_INPUT" ]] && is_truthy "$FORCE_RESTART_INPUT"; then
  FORCE_RESTART="true"
fi

if [[ "$UPDATE_A2A" == "true" ]]; then
  "${SCRIPT_DIR}/deploy/update_a2a.sh"
fi

"${SCRIPT_DIR}/deploy/install_units.sh"
"${SCRIPT_DIR}/deploy/setup_instance.sh" "$PROJECT_NAME"
FORCE_RESTART="$FORCE_RESTART" "${SCRIPT_DIR}/deploy/enable_instance.sh" "$PROJECT_NAME"
