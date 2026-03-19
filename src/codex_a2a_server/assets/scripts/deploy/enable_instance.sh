#!/usr/bin/env bash
# Enable and start the codex-a2a systemd service for a project.
# Usage: ./enable_instance.sh <project_name>
# Requires sudo to manage systemd services.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../shell_helpers.sh
source "${SCRIPT_DIR}/../shell_helpers.sh"

PROJECT_NAME="${1:-}"

if [[ -z "$PROJECT_NAME" ]]; then
  echo "Usage: $0 <project_name>" >&2
  exit 1
fi

FORCE_RESTART="${FORCE_RESTART:-false}"
: "${DATA_ROOT:=/data/codex-a2a}"
: "${A2A_HOST:=127.0.0.1}"
: "${A2A_PORT:=8000}"
: "${A2A_ENABLE_HEALTH_ENDPOINT:=true}"
: "${DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS:=30}"
: "${DEPLOY_HEALTHCHECK_INTERVAL_SECONDS:=1}"
A2A_HEALTHCHECK_URL="${A2A_HEALTHCHECK_URL:-}"
A2A_HEALTHCHECK_AUTH_HEADER_FILE=""

cleanup_healthcheck_auth() {
  if [[ -n "$A2A_HEALTHCHECK_AUTH_HEADER_FILE" ]]; then
    rm -f "$A2A_HEALTHCHECK_AUTH_HEADER_FILE"
  fi
}

trap cleanup_healthcheck_auth EXIT

require_positive_integer() {
  local key="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || [[ "$value" == "0" ]]; then
    echo "${key} must be a positive integer, got: ${value}" >&2
    exit 1
  fi
}

resolve_healthcheck_host() {
  case "${A2A_HOST}" in
    0.0.0.0|::|[::])
      echo "127.0.0.1"
      ;;
    *)
      echo "${A2A_HOST}"
      ;;
  esac
}

resolve_healthcheck_bearer_token() {
  if [[ -n "${A2A_BEARER_TOKEN:-}" ]]; then
    printf '%s' "${A2A_BEARER_TOKEN}"
    return 0
  fi

  local secret_file="${DATA_ROOT}/${PROJECT_NAME}/config/a2a.secret.env"
  if ! sudo test -f "$secret_file"; then
    echo "Missing Bearer Token secret file for health probe: ${secret_file}" >&2
    exit 1
  fi

  local token=""
  token="$(sudo sed -n 's/^A2A_BEARER_TOKEN=//p' "$secret_file" | head -n 1)"
  if [[ -z "$token" ]]; then
    echo "A2A_BEARER_TOKEN is not defined in ${secret_file}" >&2
    exit 1
  fi

  printf '%s' "$token"
}

prepare_healthcheck_auth_header() {
  local token="$1"
  local header_file
  header_file="$(mktemp)"
  chmod 600 "$header_file"
  printf 'Authorization: Bearer %s\n' "$token" >"$header_file"
  A2A_HEALTHCHECK_AUTH_HEADER_FILE="$header_file"
}

wait_for_health() {
  local timeout="$1"
  local interval="$2"
  local elapsed=0
  local response=""
  while (( elapsed < timeout )); do
    if response="$(curl -fsS -H "@${A2A_HEALTHCHECK_AUTH_HEADER_FILE}" "$A2A_HEALTHCHECK_URL" 2>/dev/null)" && [[ "$response" == *'"status"'*'"ok"'* ]]; then
      return 0
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done
  echo "Timed out waiting for authenticated /health probe: ${A2A_HEALTHCHECK_URL}" >&2
  exit 1
}

ensure_sudo_ready
require_positive_integer "DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS" "$DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS"
require_positive_integer "DEPLOY_HEALTHCHECK_INTERVAL_SECONDS" "$DEPLOY_HEALTHCHECK_INTERVAL_SECONDS"

sudo systemctl daemon-reload

start_or_restart() {
  local unit="$1"
  if [[ "$FORCE_RESTART" == "true" ]]; then
    if sudo systemctl is-active --quiet "$unit"; then
      sudo systemctl restart "$unit"
    else
      sudo systemctl enable --now "$unit"
    fi
  else
    sudo systemctl enable --now "$unit"
  fi
}

start_or_restart "codex-a2a@${PROJECT_NAME}.service"

if [[ "${A2A_ENABLE_HEALTH_ENDPOINT,,}" == "true" ]]; then
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl not found in PATH; cannot probe authenticated /health" >&2
    exit 1
  fi
  A2A_HEALTHCHECK_URL="${A2A_HEALTHCHECK_URL:-http://$(resolve_healthcheck_host):${A2A_PORT}/health}"
  prepare_healthcheck_auth_header "$(resolve_healthcheck_bearer_token)"
  wait_for_health "$DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS" "$DEPLOY_HEALTHCHECK_INTERVAL_SECONDS"
fi

sudo systemctl status "codex-a2a@${PROJECT_NAME}.service" --no-pager
