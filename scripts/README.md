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
  bootstrap host prerequisites and install the published `codex-a2a-server`
  runtime for managed systemd deployment.
- [`scripts/deploy.sh`](./deploy.sh):
  create or update one long-running `codex-a2a@.service` instance backed by the
  published package runtime.
- [`scripts/uninstall.sh`](./uninstall.sh):
  remove one deployed instance (preview-first, explicit confirm required).
- [`scripts/smoke_test_built_cli.sh`](./smoke_test_built_cli.sh):
  validate that a built wheel can be installed through `uv tool` and becomes
  healthy.

## Quick Links

- [`scripts/init_system.sh`](./init_system.sh)
- [`scripts/deploy.sh`](./deploy.sh)
- [`scripts/uninstall.sh`](./uninstall.sh)
- [`scripts/smoke_test_built_cli.sh`](./smoke_test_built_cli.sh)

## Notes

- End-user self-start no longer uses repository scripts. Prefer the published
  CLI commands documented in [README.md](../README.md) and
  [docs/guide.md](../docs/guide.md).
- `scripts/deploy/` contains internal helpers orchestrated by
  `scripts/deploy.sh`.
- `scripts/smoke_test_built_cli.sh` validates that the built wheel can be installed by
  `uv tool` and that the released CLI becomes healthy.
- Keep long-form documentation changes in `docs/` to avoid divergence.
