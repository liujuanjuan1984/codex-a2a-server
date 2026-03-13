#!/usr/bin/env bash
# Create project user, directories, and env files for systemd services.
# Usage: [GH_TOKEN=<token>] [A2A_BEARER_TOKEN=<token>] [ENABLE_SECRET_PERSISTENCE=true] ./setup_instance.sh <project_name>
# Requires env: DATA_ROOT, CODEX_BIND_HOST, CODEX_BIND_PORT, CODEX_LOG_LEVEL,
#               A2A_HOST, A2A_PORT, A2A_PUBLIC_URL.
# Optional provider secret env: see scripts/deploy/provider_secret_env_keys.sh
# Secret persistence is opt-in via ENABLE_SECRET_PERSISTENCE=true.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/provider_secret_env_keys.sh"

PROJECT_NAME="${1:-}"

if [[ "$#" -ne 1 || -z "$PROJECT_NAME" ]]; then
  echo "Usage: [GH_TOKEN=<token>] [A2A_BEARER_TOKEN=<token>] [ENABLE_SECRET_PERSISTENCE=true] $0 <project_name>" >&2
  exit 1
fi

: "${DATA_ROOT:?}"
: "${CODEX_BIND_HOST:?}"
: "${CODEX_BIND_PORT:?}"
: "${CODEX_LOG_LEVEL:?}"
: "${A2A_HOST:?}"
: "${A2A_PORT:?}"
: "${A2A_PUBLIC_URL:?}"
: "${A2A_STREAMING:=true}"
: "${ENABLE_SECRET_PERSISTENCE:=false}"

PROJECT_DIR="${DATA_ROOT}/${PROJECT_NAME}"
WORKSPACE_DIR="${PROJECT_DIR}/workspace"
CONFIG_DIR="${PROJECT_DIR}/config"
CODEX_AUTH_ENV_FILE="${CONFIG_DIR}/codex.auth.env"
CODEX_SECRET_ENV_FILE="${CONFIG_DIR}/codex.secret.env"
A2A_SECRET_ENV_FILE="${CONFIG_DIR}/a2a.secret.env"
LOG_DIR="${PROJECT_DIR}/logs"
RUN_DIR="${PROJECT_DIR}/run"
ASKPASS_SCRIPT="${RUN_DIR}/git-askpass.sh"
CACHE_DIR="${PROJECT_DIR}/.cache/codex"
LOCAL_DIR="${PROJECT_DIR}/.local"
STATE_DIR="${LOCAL_DIR}/state"
CODEX_LOCAL_SHARE_DIR="${PROJECT_DIR}/.local/share/codex"
CODEX_BIN_DIR="${CODEX_LOCAL_SHARE_DIR}/bin"
DATA_DIR="${PROJECT_DIR}/.local/share/codex/storage/session"
SECRET_ENV_KEYS=("${PROVIDER_SECRET_ENV_KEYS[@]}")

is_truthy() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

PERSIST_SECRETS="false"
if is_truthy "${ENABLE_SECRET_PERSISTENCE}"; then
  PERSIST_SECRETS="true"
fi

# DATA_ROOT must be traversable by the per-project system user. In hardened
# deployments, using a non-traversable DATA_ROOT (missing o+x) will break
# Codex writes to $HOME/.cache, $HOME/.local/share, and $HOME/.local/state.
ensure_data_root_accessible() {
  local root="$1"
  if ! sudo test -d "$root"; then
    sudo install -d -m 711 -o root -g root "$root"
    return 0
  fi
  local mode
  mode="$(sudo stat -c '%a' "$root" 2>/dev/null || echo "")"
  if [[ ! "$mode" =~ ^[0-9]{3,4}$ ]]; then
    echo "Unable to stat DATA_ROOT: ${root}" >&2
    exit 1
  fi
  local other=$((mode % 10))
  if (( (other & 1) == 0 )); then
    echo "DATA_ROOT is not traversable by project users: ${root} (mode=${mode})." >&2
    echo "Fix: choose a different DATA_ROOT (recommended: /data/codex-a2a) or chmod o+x on DATA_ROOT." >&2
    exit 1
  fi
}

ensure_data_root_accessible "$DATA_ROOT"

get_user_home() {
  getent passwd "$1" | awk -F: '{print $6}'
}

ensure_user_home_matches_project_dir() {
  # This deploy workflow expects each instance user to have HOME=${DATA_ROOT}/<project>.
  # If an operator previously deployed with a different DATA_ROOT, we fail fast to
  # avoid subtle systemd/unit mismatches and permission issues.
  local user="$1"
  local expected_home="$2"
  if ! id "$user" &>/dev/null; then
    return 0
  fi
  local current_home
  current_home="$(get_user_home "$user")"
  if [[ -z "$current_home" ]]; then
    echo "Unable to determine home directory for user: ${user}" >&2
    exit 1
  fi
  if [[ "$current_home" != "$expected_home" ]]; then
    echo "Existing user ${user} has a different home directory than expected:" >&2
    echo "  current:  ${current_home}" >&2
    echo "  expected: ${expected_home}" >&2
    echo "" >&2
    echo "This deploy script does not migrate instances automatically." >&2
    echo "Fix: uninstall/recreate the instance user, or migrate explicitly, then re-run deploy." >&2
    exit 1
  fi
}

ensure_user_home_matches_project_dir "$PROJECT_NAME" "$PROJECT_DIR"

if ! id "$PROJECT_NAME" &>/dev/null; then
  sudo adduser --system --group --home "$PROJECT_DIR" "$PROJECT_NAME"
fi

sudo install -d -m 700 -o "$PROJECT_NAME" -g "$PROJECT_NAME" "$PROJECT_DIR" "$WORKSPACE_DIR" "$LOG_DIR" "$RUN_DIR"
sudo install -d -m 700 -o root -g root "$CONFIG_DIR"
# Ensure Codex can write its XDG cache/data paths under $HOME even if the
# instance was previously started with a different user (stale root-owned dirs).
sudo install -d -m 700 -o "$PROJECT_NAME" -g "$PROJECT_NAME" \
  "$CACHE_DIR" \
  "$LOCAL_DIR" \
  "$STATE_DIR" \
  "$DATA_DIR" \
  "$CODEX_BIN_DIR"
# If the directory existed with wrong ownership (e.g., started as root once),
# fix it to avoid EACCES when codex tries to mkdir under codex/.
sudo chown -R "$PROJECT_NAME:$PROJECT_NAME" "$CACHE_DIR" "$STATE_DIR" "$CODEX_LOCAL_SHARE_DIR"

codex_auth_example_tmp="$(mktemp)"
cat <<'EOF' >"$codex_auth_example_tmp"
# Root-only runtime secret file for codex@.service.
# Populate GH_TOKEN here if ENABLE_SECRET_PERSISTENCE is not enabled during deploy.
GH_TOKEN=<github-token>
EOF
sudo install -m 600 -o root -g root "$codex_auth_example_tmp" "$CONFIG_DIR/codex.auth.env.example"
rm -f "$codex_auth_example_tmp"

a2a_secret_example_tmp="$(mktemp)"
cat <<'EOF' >"$a2a_secret_example_tmp"
# Root-only runtime secret file for codex-a2a@.service.
# Populate A2A_BEARER_TOKEN here if ENABLE_SECRET_PERSISTENCE is not enabled during deploy.
A2A_BEARER_TOKEN=<a2a-bearer-token>
EOF
sudo install -m 600 -o root -g root "$a2a_secret_example_tmp" "$CONFIG_DIR/a2a.secret.env.example"
rm -f "$a2a_secret_example_tmp"

codex_secret_example_tmp="$(mktemp)"
{
  echo "# Optional root-only provider secret file for codex@.service."
  echo "# Populate only the provider keys your deployment actually uses."
  for key in "${SECRET_ENV_KEYS[@]}"; do
    echo "${key}=<optional>"
  done
} >"$codex_secret_example_tmp"
sudo install -m 600 -o root -g root "$codex_secret_example_tmp" "$CONFIG_DIR/codex.secret.env.example"
rm -f "$codex_secret_example_tmp"

askpass_tmp="$(mktemp)"
cat <<'SCRIPT' >"$askpass_tmp"
#!/usr/bin/env bash
case "$1" in
  *Username*) echo "x-access-token" ;;
  *Password*) echo "${GH_TOKEN}" ;;
  *) echo "" ;;
esac
SCRIPT
sudo install -m 700 -o "$PROJECT_NAME" -g "$PROJECT_NAME" "$askpass_tmp" "$ASKPASS_SCRIPT"
rm -f "$askpass_tmp"

git_author_name="Codex-${PROJECT_NAME}"
git_author_email="${PROJECT_NAME}@example.com"
if [[ -n "${GIT_IDENTITY_NAME:-}" ]]; then
  git_author_name="${GIT_IDENTITY_NAME}"
fi
if [[ -n "${GIT_IDENTITY_EMAIL:-}" ]]; then
  git_author_email="${GIT_IDENTITY_EMAIL}"
fi

codex_env_tmp="$(mktemp)"
{
  echo "CODEX_LOG_LEVEL=${CODEX_LOG_LEVEL}"
  echo "CODEX_BIND_HOST=${CODEX_BIND_HOST}"
  echo "CODEX_BIND_PORT=${CODEX_BIND_PORT}"
  echo "CODEX_EXTRA_ARGS=${CODEX_EXTRA_ARGS:-}"
  echo "GIT_ASKPASS=${ASKPASS_SCRIPT}"
  echo "GIT_ASKPASS_REQUIRE=force"
  echo "GIT_TERMINAL_PROMPT=0"
  echo "GIT_AUTHOR_NAME=${git_author_name}"
  echo "GIT_COMMITTER_NAME=${git_author_name}"
  echo "GIT_AUTHOR_EMAIL=${git_author_email}"
  echo "GIT_COMMITTER_EMAIL=${git_author_email}"
  if [[ -n "${CODEX_PROVIDER_ID:-}" ]]; then
    echo "CODEX_PROVIDER_ID=${CODEX_PROVIDER_ID}"
  fi
  if [[ -n "${CODEX_MODEL_ID:-}" ]]; then
    echo "CODEX_MODEL_ID=${CODEX_MODEL_ID}"
  fi
} >"$codex_env_tmp"
sudo install -m 600 -o root -g root "$codex_env_tmp" "$CONFIG_DIR/codex.env"
rm -f "$codex_env_tmp"

if [[ "$PERSIST_SECRETS" == "true" ]]; then
  : "${GH_TOKEN:?GH_TOKEN is required when ENABLE_SECRET_PERSISTENCE=true}"
  : "${A2A_BEARER_TOKEN:?A2A_BEARER_TOKEN is required when ENABLE_SECRET_PERSISTENCE=true}"

  codex_auth_env_tmp="$(mktemp)"
  {
    echo "GH_TOKEN=${GH_TOKEN}"
  } >"$codex_auth_env_tmp"
  sudo install -m 600 -o root -g root "$codex_auth_env_tmp" "$CODEX_AUTH_ENV_FILE"
  rm -f "$codex_auth_env_tmp"

  codex_secret_env_tmp="$(mktemp)"
  has_secret_entry=0
  for key in "${SECRET_ENV_KEYS[@]}"; do
    value="${!key:-}"
    if [[ -z "$value" && -f "$CODEX_SECRET_ENV_FILE" ]]; then
      value="$(sed -n "s/^${key}=//p" "$CODEX_SECRET_ENV_FILE" | head -n 1)"
    fi
    if [[ -n "$value" ]]; then
      printf '%s=%s\n' "$key" "$value" >>"$codex_secret_env_tmp"
      has_secret_entry=1
    fi
  done
  if [[ "$has_secret_entry" -eq 1 ]]; then
    sudo install -m 600 -o root -g root "$codex_secret_env_tmp" "$CODEX_SECRET_ENV_FILE"
  fi
  rm -f "$codex_secret_env_tmp"
else
  echo "ENABLE_SECRET_PERSISTENCE is disabled; deploy will not write GH_TOKEN, A2A_BEARER_TOKEN, or provider keys to disk." >&2
  echo "Provision root-only runtime secret files under ${CONFIG_DIR} before starting services:" >&2
  echo "  - codex.auth.env (required: GH_TOKEN)" >&2
  echo "  - a2a.secret.env (required: A2A_BEARER_TOKEN)" >&2
  echo "  - codex.secret.env (optional provider keys, if your Codex provider requires them)" >&2
  echo "Templates were generated as *.example files in ${CONFIG_DIR}." >&2
fi

a2a_env_tmp="$(mktemp)"
{
  echo "A2A_HOST=${A2A_HOST}"
  echo "A2A_PORT=${A2A_PORT}"
  echo "A2A_PUBLIC_URL=${A2A_PUBLIC_URL}"
  echo "A2A_PROJECT=${PROJECT_NAME}"
  echo "A2A_STREAMING=${A2A_STREAMING}"
  echo "A2A_LOG_LEVEL=${A2A_LOG_LEVEL:-INFO}"
  echo "A2A_LOG_PAYLOADS=${A2A_LOG_PAYLOADS:-false}"
  echo "A2A_LOG_BODY_LIMIT=${A2A_LOG_BODY_LIMIT:-0}"
  echo "CODEX_BASE_URL=http://${CODEX_BIND_HOST}:${CODEX_BIND_PORT}"
  echo "CODEX_DIRECTORY=${WORKSPACE_DIR}"
  echo "CODEX_TIMEOUT=${CODEX_TIMEOUT:-300}"
  if [[ -n "${CODEX_TIMEOUT_STREAM:-}" ]]; then
    echo "CODEX_TIMEOUT_STREAM=${CODEX_TIMEOUT_STREAM}"
  fi
  if [[ -n "${CODEX_PROVIDER_ID:-}" ]]; then
    echo "CODEX_PROVIDER_ID=${CODEX_PROVIDER_ID}"
  fi
  if [[ -n "${CODEX_MODEL_ID:-}" ]]; then
    echo "CODEX_MODEL_ID=${CODEX_MODEL_ID}"
  fi
} >"$a2a_env_tmp"
sudo install -m 600 -o root -g root "$a2a_env_tmp" "$CONFIG_DIR/a2a.env"
rm -f "$a2a_env_tmp"

if [[ "$PERSIST_SECRETS" == "true" ]]; then
  a2a_secret_env_tmp="$(mktemp)"
  {
    echo "A2A_BEARER_TOKEN=${A2A_BEARER_TOKEN}"
  } >"$a2a_secret_env_tmp"
  sudo install -m 600 -o root -g root "$a2a_secret_env_tmp" "$A2A_SECRET_ENV_FILE"
  rm -f "$a2a_secret_env_tmp"
fi

require_runtime_secret_file() {
  local file="$1"
  local key="$2"
  local example="$3"
  if ! sudo test -f "$file"; then
    echo "Missing required runtime secret file: ${file}" >&2
    echo "Copy and edit the template: ${example}" >&2
    exit 1
  fi
  if ! sudo grep -q "^${key}=" "$file"; then
    echo "Runtime secret file does not define ${key}: ${file}" >&2
    echo "See template: ${example}" >&2
    exit 1
  fi
}

require_runtime_secret_file "$CODEX_AUTH_ENV_FILE" "GH_TOKEN" "$CONFIG_DIR/codex.auth.env.example"
require_runtime_secret_file "$A2A_SECRET_ENV_FILE" "A2A_BEARER_TOKEN" "$CONFIG_DIR/a2a.secret.env.example"

if command -v gh >/dev/null 2>&1; then
  sudo install -d -m 700 -o "$PROJECT_NAME" -g "$PROJECT_NAME" \
    "${PROJECT_DIR}/.config" "${PROJECT_DIR}/.config/gh"
  if [[ -n "${GH_TOKEN:-}" ]]; then
    if ! printf '%s' "$GH_TOKEN" | sudo -u "$PROJECT_NAME" -H \
      gh auth login --hostname github.com --with-token >/dev/null 2>&1; then
      echo "gh auth login failed for ${PROJECT_NAME}" >&2
      exit 1
    fi
  else
    echo "GH_TOKEN not provided to setup_instance.sh; skipping gh auth login during deploy." >&2
  fi
else
  echo "gh not found; skipping gh auth setup." >&2
fi

if [[ -n "${REPO_URL:-}" ]]; then
  if sudo -u "$PROJECT_NAME" -H test -d "${WORKSPACE_DIR}/.git"; then
    echo "Workspace already initialized; skipping clone."
  elif [[ -n "$(sudo -u "$PROJECT_NAME" -H ls -A "$WORKSPACE_DIR" 2>/dev/null)" ]]; then
    echo "Workspace is not empty; skipping clone." >&2
  else
    clone_args=("$REPO_URL" "$WORKSPACE_DIR")
    if [[ -n "${REPO_BRANCH:-}" ]]; then
      clone_args=(--branch "$REPO_BRANCH" --single-branch "${clone_args[@]}")
    fi
    if [[ -n "${GH_TOKEN:-}" ]]; then
      sudo -u "$PROJECT_NAME" -H env \
        GH_TOKEN="$GH_TOKEN" \
        GIT_ASKPASS="$ASKPASS_SCRIPT" \
        GIT_ASKPASS_REQUIRE=force \
        GIT_TERMINAL_PROMPT=0 \
        git clone "${clone_args[@]}"
    else
      sudo -u "$PROJECT_NAME" -H git clone "${clone_args[@]}"
    fi
  fi
fi
