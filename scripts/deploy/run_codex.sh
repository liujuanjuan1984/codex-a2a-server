#!/usr/bin/env bash
# Wrapper to run codex serve with configured host/port/logging.
set -euo pipefail

CODEX_CORE_DIR="${CODEX_CORE_DIR:-/opt/.codex}"
CODEX_BIN="${CODEX_BIN:-${CODEX_CORE_DIR}/bin/codex}"
CODEX_LOG_LEVEL="${CODEX_LOG_LEVEL:-INFO}"
CODEX_BIND_HOST="${CODEX_BIND_HOST:-127.0.0.1}"
CODEX_BIND_PORT="${CODEX_BIND_PORT:-4096}"
CODEX_EXTRA_ARGS="${CODEX_EXTRA_ARGS:-}"
CODEX_PROVIDER_ID="${CODEX_PROVIDER_ID:-}"
CODEX_MODEL_ID="${CODEX_MODEL_ID:-}"
CODEX_LSP="${CODEX_LSP:-false}"
GOOGLE_GENERATIVE_AI_API_KEY="${GOOGLE_GENERATIVE_AI_API_KEY:-}"

if [[ ! -x "$CODEX_BIN" ]]; then
  echo "codex binary not found at $CODEX_BIN" >&2
  exit 1
fi

provider_lc="${CODEX_PROVIDER_ID,,}"
model_lc="${CODEX_MODEL_ID,,}"
if [[ "$provider_lc" == "google" || "$model_lc" == *"gemini"* ]]; then
  if [[ -z "$GOOGLE_GENERATIVE_AI_API_KEY" ]]; then
    echo "GOOGLE_GENERATIVE_AI_API_KEY is required when using Google/Gemini model settings" >&2
    exit 1
  fi
fi

if [[ -z "${CODEX_CONFIG_CONTENT:-}" ]]; then
  case "${CODEX_LSP,,}" in
    1|true|yes|on)
      lsp_json=true
      ;;
    0|false|no|off|"")
      lsp_json=false
      ;;
    *)
      echo "Invalid CODEX_LSP value: ${CODEX_LSP} (expected true/false)" >&2
      exit 1
      ;;
  esac
  printf -v CODEX_CONFIG_CONTENT \
    '{"$schema":"https://codex.ai/config.json","lsp":%s}' \
    "$lsp_json"
fi

# Upstream Codex SDK/runtime still consumes OPENCODE_CONFIG_CONTENT.
export OPENCODE_CONFIG_CONTENT="$CODEX_CONFIG_CONTENT"

cmd=("$CODEX_BIN" serve --log-level "$CODEX_LOG_LEVEL" --print-logs)

if [[ -n "$CODEX_BIND_HOST" ]]; then
  cmd+=(--hostname "$CODEX_BIND_HOST")
fi

if [[ -n "$CODEX_BIND_PORT" ]]; then
  cmd+=(--port "$CODEX_BIND_PORT")
fi

if [[ -n "$CODEX_EXTRA_ARGS" ]]; then
  read -r -a extra_args <<<"$CODEX_EXTRA_ARGS"
  cmd+=("${extra_args[@]}")
fi

exec "${cmd[@]}"
