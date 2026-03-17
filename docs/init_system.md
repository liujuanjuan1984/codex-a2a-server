# System Bootstrap Script (`init_system.sh`)

This document describes `scripts/init_system.sh`. The script prepares shared
system prerequisites for systemd-based Codex + A2A deployment and installs the
published `codex-a2a-server` runtime into a shared virtual environment. It is
idempotent: completed steps are automatically skipped.

## Usage

Run directly:

```bash
./scripts/init_system.sh
```

The script does not accept runtime arguments. To adjust paths, feature toggles, or versions, edit the constants at the top of `scripts/init_system.sh`.

## What It Does

- Installs base tooling and `gh` (GitHub CLI) from official sources.
- Installs Node.js >= 20 (`npm`/`npx`) using NodeSource or distro packages.
- Installs `uv` and pre-downloads Python versions
  `3.10/3.11/3.12/3.13` (only if missing).
- Creates shared directories and applies permissions
  (`/opt/uv-python` starts as `777`, then becomes recursively `755` after
  pre-download).
- Creates the shared runtime virtual environment
  (`/opt/codex-a2a/runtime` by default).
- Installs the published `codex-a2a-server` package into that shared runtime.
- Fails fast if `systemd` (`systemctl`) is unavailable.
- If Codex installer places files in `/root/.codex`, moves them to
  `CODEX_CORE_DIR` and writes `/usr/local/bin/codex`.

## Customization

Edit the constant block at the top of `scripts/init_system.sh`. Common values:

- Paths: `CODEX_CORE_DIR`, `CODEX_A2A_ROOT`, `CODEX_A2A_RUNTIME_DIR`,
  `UV_PYTHON_DIR`, `DATA_ROOT`
- Permissions: `UV_PYTHON_DIR_MODE`, `UV_PYTHON_DIR_FINAL_MODE`,
  `UV_PYTHON_DIR_GROUP`
- Published package: `CODEX_A2A_PACKAGE_SPEC`, `CODEX_A2A_PYTHON_VERSION`
- Toggles: `INSTALL_PACKAGES`, `INSTALL_UV`, `INSTALL_GH`, `INSTALL_NODE`
- Versions: `NODE_MAJOR`, `UV_PYTHON_VERSIONS`
