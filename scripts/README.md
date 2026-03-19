# scripts

Repository-maintainer scripts live here.

This document only explains the remaining repository-local maintainer scripts.
User-facing runtime and managed deploy entrypoints now live in the released
`codex-a2a-server` CLI, and host-level bootstrap or uninstall flows are out of
scope for this repository.

## Start Here

- [Project overview](../README.md)
- [Architecture guide](../docs/architecture.md)
- [Usage guide](../docs/guide.md)
- [Deployment guide](../docs/deployment.md)

## Which Script to Use

- [`scripts/smoke_test_built_cli.sh`](./smoke_test_built_cli.sh):
  validate that a built wheel can be installed through `uv tool` and becomes
  healthy.
- [`scripts/sync_codex_docs.sh`](./sync_codex_docs.sh):
  refresh vendored Codex documentation snapshots used by this repository.

## Quick Links

- [`scripts/smoke_test_built_cli.sh`](./smoke_test_built_cli.sh)
- [`scripts/sync_codex_docs.sh`](./sync_codex_docs.sh)

## Notes

- End-user self-start and managed deployment no longer use repository scripts.
  Prefer the published CLI commands documented in [README.md](../README.md),
  [docs/guide.md](../docs/guide.md), and [docs/deployment.md](../docs/deployment.md).
- Host bootstrap and uninstall flows are intentionally not shipped as product
  entrypoints. Treat those operations as deployment-specific operator tooling.
- Bootstrap or uninstall flows are out of scope for this repository runtime surface.
- Managed deployment uses `codex-a2a-server deploy`, including authenticated
  `/health` readiness handling in the packaged deploy assets.
- The packaged deploy path still performs authenticated `/health` readiness
  checks when the health endpoint is enabled.
- Package-internal deploy helpers now live only under
  `src/codex_a2a_server/assets/scripts/`.
- `scripts/smoke_test_built_cli.sh` validates that the built wheel can be installed by
  `uv tool` and that the released CLI becomes healthy.
- Keep long-form documentation changes in `docs/` to avoid divergence.
