# scripts

Executable scripts live here. This file is the primary script-entry guide.

This document only explains script entrypoints. It does not repeat project
overview, runtime contracts, or deployment rationale in detail.

## Start Here

- [Project overview](../README.md)
- [Architecture guide](../docs/architecture.md)
- [Usage guide](../docs/guide.md)
- [Deployment guide](../docs/deployment.md)

## Which Script to Use

- [`scripts/init_system.sh`](./init_system.sh):
  bootstrap host prerequisites for systemd deployment.
- [`scripts/deploy.sh`](./deploy.sh):
  create/update one long-running systemd instance.
- [`scripts/deploy_light.sh`](./deploy_light.sh):
  lightweight foreground runner on current user (no system user/workspace setup).
  It preserves exported `CODEX_*` shell variables, inherits local
  `~/.codex/config.toml` by default, supports explicit instance overrides such
  as `codex_model=...` and
  `codex_model_reasoning_effort=...`, and blocks known-invalid model /
  reasoning combinations before launch. It stays in the foreground and writes
  logs to stdout/stderr so callers can decide whether to use `nohup`, `pm2`,
  `systemd`, or another supervisor for detached execution and log capture. See
  [`docs/guide.md`](../docs/guide.md) for concrete `nohup` and `pm2` examples.
- [`scripts/start_services.sh`](./start_services.sh):
  local foreground runner without systemd.
- [`scripts/uninstall.sh`](./uninstall.sh):
  remove one deployed instance (preview-first, explicit confirm required).

## Quick Links

- [`scripts/init_system.sh`](./init_system.sh)
- [`scripts/deploy.sh`](./deploy.sh)
- [`scripts/deploy_light.sh`](./deploy_light.sh)
- [`scripts/start_services.sh`](./start_services.sh)
- [`scripts/uninstall.sh`](./uninstall.sh)
- [`scripts/smoke_test_built_cli.sh`](./smoke_test_built_cli.sh)

## Notes

- `scripts/deploy/` contains helper scripts orchestrated by `scripts/deploy.sh`.
- `scripts/smoke_test_built_cli.sh` validates that the built wheel can be installed by
  `uv tool` and that the released CLI becomes healthy.
- Keep long-form documentation changes in `docs/` to avoid divergence.
