# scripts

Packaged shell assets live here.

This document only explains the packaged script assets behind the released CLI
and repository-maintainer wrappers. It does not repeat project overview,
runtime contracts, or deployment rationale in detail.

## Start Here

- [Project overview](../README.md)
- [Architecture guide](../docs/architecture.md)
- [Usage guide](../docs/guide.md)
- [Deployment guide](../docs/deployment.md)

## Which Script to Use

- [`scripts/deploy.sh`](./deploy.sh):
  internal implementation behind `codex-a2a-server deploy`; it creates or
  updates one long-running `codex-a2a@.service` instance backed by the
  published package runtime, including an authenticated `/health` readiness
  probe when the health endpoint is enabled.
- [`scripts/smoke_test_built_cli.sh`](./smoke_test_built_cli.sh):
  validate that a built wheel can be installed through `uv tool` and becomes
  healthy.

## Quick Links

- [`scripts/deploy.sh`](./deploy.sh)
- [`scripts/smoke_test_built_cli.sh`](./smoke_test_built_cli.sh)

## Notes

- End-user self-start no longer uses repository scripts. Prefer the published
  CLI commands documented in [README.md](../README.md) and
  [docs/guide.md](../docs/guide.md).
- Host bootstrap and uninstall flows are intentionally out of product scope.
- For managed release-based deployment, use `codex-a2a-server deploy`.
- `scripts/deploy/` contains internal helpers orchestrated by
  `scripts/deploy.sh`.
- `scripts/smoke_test_built_cli.sh` validates that the built wheel can be installed by
  `uv tool` and that the released CLI becomes healthy.
- Keep long-form documentation changes in `docs/` to avoid divergence.
