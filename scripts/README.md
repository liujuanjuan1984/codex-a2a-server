# scripts

Repository-maintainer scripts live here.

This document only explains the remaining repository-local script entrypoints.
User-facing runtime and managed deploy entrypoints now live in the released
`codex-a2a-server` CLI.

## Start Here

- [Project overview](../README.md)
- [Architecture guide](../docs/architecture.md)
- [Usage guide](../docs/guide.md)
- [Deployment guide](../docs/deployment.md)

## Which Script to Use

- [`scripts/init_system.sh`](./init_system.sh):
  bootstrap host prerequisites and install the published `codex-a2a-server`
  runtime for managed systemd deployment.
- [`scripts/uninstall.sh`](./uninstall.sh):
  remove one deployed instance (preview-first, explicit confirm required).
- [`scripts/smoke_test_built_cli.sh`](./smoke_test_built_cli.sh):
  validate that a built wheel can be installed through `uv tool` and becomes
  healthy.
- [`scripts/sync_codex_docs.sh`](./sync_codex_docs.sh):
  refresh vendored Codex documentation snapshots used by this repository.

## Quick Links

- [`scripts/init_system.sh`](./init_system.sh)
- [`scripts/uninstall.sh`](./uninstall.sh)
- [`scripts/smoke_test_built_cli.sh`](./smoke_test_built_cli.sh)
- [`scripts/sync_codex_docs.sh`](./sync_codex_docs.sh)

## Notes

- End-user self-start and managed deployment no longer use repository scripts.
  Prefer the published CLI commands documented in [README.md](../README.md),
  [docs/guide.md](../docs/guide.md), and [docs/deployment.md](../docs/deployment.md).
- Managed deployment uses `codex-a2a-server deploy`, including authenticated
  `/health` readiness handling in the packaged deploy assets.
- The packaged deploy path still performs authenticated `/health` readiness
  checks when the health endpoint is enabled.
- Package-internal deploy helpers now live only under
  `src/codex_a2a_server/assets/scripts/`.
- `scripts/smoke_test_built_cli.sh` validates that the built wheel can be installed by
  `uv tool` and that the released CLI becomes healthy.
- Keep long-form documentation changes in `docs/` to avoid divergence.
