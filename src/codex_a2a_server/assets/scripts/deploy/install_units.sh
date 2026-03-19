#!/usr/bin/env bash
# Install the systemd template unit for codex-a2a-server.
# Requires env: CODEX_A2A_RUNTIME_DIR, CODEX_CORE_DIR, UV_PYTHON_DIR, DATA_ROOT.
# Requires sudo to write /etc/systemd/system.
set -euo pipefail

: "${CODEX_A2A_RUNTIME_DIR:?}"
: "${CODEX_CORE_DIR:?}"
: "${UV_PYTHON_DIR:?}"
: "${DATA_ROOT:?}"

UNIT_DIR="/etc/systemd/system"
A2A_UNIT="${UNIT_DIR}/codex-a2a@.service"
A2A_BIN="${CODEX_A2A_RUNTIME_DIR}/bin/codex-a2a-server"

if [[ ! -x "$A2A_BIN" ]]; then
  echo "codex-a2a-server runtime binary not found: ${A2A_BIN}" >&2
  echo "Run codex-a2a-server deploy --project <name> --update-a2a first." >&2
  exit 1
fi

sudo install -d -m 755 "$UNIT_DIR"

cat <<UNIT | sudo tee "$A2A_UNIT" >/dev/null
[Unit]
Description=Codex A2A for %i
After=network.target

[Service]
Type=simple
User=%i
Group=%i
WorkingDirectory=${DATA_ROOT}/%i
Environment=CODEX_A2A_RUNTIME_DIR=${CODEX_A2A_RUNTIME_DIR}
Environment=CODEX_CORE_DIR=${CODEX_CORE_DIR}
Environment=UV_PYTHON_DIR=${UV_PYTHON_DIR}
Environment=PATH=${CODEX_CORE_DIR}/bin:${CODEX_A2A_RUNTIME_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=${DATA_ROOT}/%i/config/codex.env
EnvironmentFile=-${DATA_ROOT}/%i/config/codex.secret.env
EnvironmentFile=${DATA_ROOT}/%i/config/a2a.env
EnvironmentFile=-${DATA_ROOT}/%i/config/a2a.secret.env
Environment=HOME=${DATA_ROOT}/%i

ExecStart=${A2A_BIN}
Restart=on-failure
RestartSec=2
UMask=0077

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${DATA_ROOT}/%i
ReadOnlyPaths=${CODEX_A2A_RUNTIME_DIR}
ReadOnlyPaths=${CODEX_CORE_DIR}
ReadOnlyPaths=${UV_PYTHON_DIR}

[Install]
WantedBy=multi-user.target
UNIT
