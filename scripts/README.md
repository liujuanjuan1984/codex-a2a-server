# scripts

Repository-maintainer scripts live here.

This document only explains the remaining repository-local maintainer scripts.
User-facing runtime entrypoints live in the released `codex-a2a-server` CLI.
If you want the fastest install/start path for the service itself, use the
`uv tool` flow in [README.md](../README.md) instead of anything in this
directory.

## Start Here

- [Project overview](../README.md)
- [Architecture guide](../docs/architecture.md)
- [Usage guide](../docs/guide.md)

## Which Script to Use

- [`scripts/smoke_test_built_cli.sh`](./smoke_test_built_cli.sh):
  validate that a built wheel can be installed through `uv tool` and becomes
  healthy.
- [`scripts/sync_codex_docs.sh`](./sync_codex_docs.sh):
  refresh vendored Codex documentation snapshots used by this repository.

## Notes

- End-user runtime startup does not use repository scripts. Prefer the
  published CLI command documented in [README.md](../README.md) and
  [docs/guide.md](../docs/guide.md).
- `scripts/smoke_test_built_cli.sh` validates that the built wheel can be installed by
  `uv tool` and that the released CLI becomes healthy.
- Keep long-form documentation changes in `docs/` to avoid divergence.
