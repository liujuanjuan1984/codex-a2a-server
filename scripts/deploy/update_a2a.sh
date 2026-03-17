#!/usr/bin/env bash
# Update the shared codex-a2a-server runtime from a published package.
# Requires env: CODEX_A2A_RUNTIME_DIR.
set -euo pipefail

: "${CODEX_A2A_RUNTIME_DIR:?}"

CODEX_A2A_PACKAGE_SPEC="${CODEX_A2A_PACKAGE_SPEC:-codex-a2a-server}"
CODEX_A2A_PYTHON_VERSION="${CODEX_A2A_PYTHON_VERSION:-3.13}"
RUNTIME_PYTHON="${CODEX_A2A_RUNTIME_DIR}/bin/python"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found in PATH; cannot update runtime" >&2
  exit 1
fi

if [[ ! -x "$RUNTIME_PYTHON" ]]; then
  echo "Creating runtime virtualenv in ${CODEX_A2A_RUNTIME_DIR}..."
  uv venv "$CODEX_A2A_RUNTIME_DIR" --python "$CODEX_A2A_PYTHON_VERSION"
fi

echo "Installing ${CODEX_A2A_PACKAGE_SPEC} into ${CODEX_A2A_RUNTIME_DIR}..."
uv pip install --python "$RUNTIME_PYTHON" --upgrade "$CODEX_A2A_PACKAGE_SPEC"
