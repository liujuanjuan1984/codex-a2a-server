#!/usr/bin/env bash
# Install systemd template units for Codex and A2A.
# Requires env: CODEX_A2A_DIR, CODEX_CORE_DIR, UV_PYTHON_DIR, DATA_ROOT.
# Requires sudo to write /etc/systemd/system.
set -euo pipefail

: "${CODEX_A2A_DIR:?}"
: "${CODEX_CORE_DIR:?}"
: "${UV_PYTHON_DIR:?}"
: "${DATA_ROOT:?}"

UNIT_DIR="/etc/systemd/system"
CODEX_UNIT="${UNIT_DIR}/codex@.service"
A2A_UNIT="${UNIT_DIR}/codex-a2a@.service"

sudo install -d -m 755 "$UNIT_DIR"

cat <<UNIT | sudo tee "$CODEX_UNIT" >/dev/null
[Unit]
Description=Codex serve for %i
After=network.target

[Service]
Type=simple
User=%i
Group=%i
WorkingDirectory=${DATA_ROOT}/%i
Environment=CODEX_CORE_DIR=${CODEX_CORE_DIR}
Environment=CODEX_A2A_DIR=${CODEX_A2A_DIR}
Environment=UV_PYTHON_DIR=${UV_PYTHON_DIR}
Environment=PATH=${CODEX_CORE_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=${DATA_ROOT}/%i/config/codex.env
EnvironmentFile=-${DATA_ROOT}/%i/config/codex.auth.env
EnvironmentFile=-${DATA_ROOT}/%i/config/codex.secret.env
Environment=HOME=${DATA_ROOT}/%i

ExecStart=${CODEX_A2A_DIR}/scripts/deploy/run_codex.sh
Restart=on-failure
RestartSec=2
UMask=0077

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${DATA_ROOT}/%i
ReadOnlyPaths=${CODEX_CORE_DIR}
ReadOnlyPaths=${CODEX_A2A_DIR}
ReadOnlyPaths=${UV_PYTHON_DIR}
ReadOnlyPaths=/usr/bin/gh

[Install]
WantedBy=multi-user.target
UNIT

cat <<UNIT | sudo tee "$A2A_UNIT" >/dev/null
[Unit]
Description=Codex A2A for %i
After=network.target codex@%i.service
Requires=codex@%i.service

[Service]
Type=simple
User=%i
Group=%i
WorkingDirectory=${DATA_ROOT}/%i
Environment=CODEX_A2A_DIR=${CODEX_A2A_DIR}
Environment=CODEX_CORE_DIR=${CODEX_CORE_DIR}
Environment=UV_PYTHON_DIR=${UV_PYTHON_DIR}
EnvironmentFile=${DATA_ROOT}/%i/config/a2a.env
EnvironmentFile=-${DATA_ROOT}/%i/config/a2a.secret.env
Environment=HOME=${DATA_ROOT}/%i

ExecStart=${CODEX_A2A_DIR}/scripts/deploy/run_a2a.sh
Restart=on-failure
RestartSec=2
UMask=0077

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${DATA_ROOT}/%i
ReadOnlyPaths=${CODEX_A2A_DIR}
ReadOnlyPaths=${CODEX_CORE_DIR}
ReadOnlyPaths=${UV_PYTHON_DIR}

[Install]
WantedBy=multi-user.target
UNIT
