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
  lightweight background runner on current user (no system user/workspace setup).
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

## Notes

- `scripts/deploy/` contains helper scripts orchestrated by `scripts/deploy.sh`.
- Keep long-form documentation changes in `docs/` to avoid divergence.
