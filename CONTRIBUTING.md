# Contributing

Thanks for contributing to `codex-a2a-server`.

## Scope

This repository is the server/runtime boundary around Codex for A2A clients.
Keep contributions aligned with that role:

- transport and contract correctness
- deployment and operations guardrails
- session, streaming, and interrupt interoperability
- security and observability for the service boundary

Client-only concerns should usually stay out of this repository.

## Workflow

1. Start from the latest `main`.
2. Work in a dedicated branch.
3. Link the change to an issue whenever the work changes runtime behavior,
   contracts, deployment, or documentation beyond small editorial cleanup.
4. Keep PRs focused and describe contract or compatibility implications
   explicitly.

## Validation Baseline

Run the default validation baseline before opening or updating a PR:

```bash
uv run pre-commit run --all-files
uv run pytest
```

For deployment or shell script changes, also run:

```bash
bash -n scripts/deploy.sh
bash -n scripts/deploy/setup_instance.sh
```

If `pre-commit` rewrites files, review the rewritten output and re-run the
checks until the working tree is clean.

## Compatibility Expectations

- The repository targets Python 3.11, 3.12, and 3.13.
- Machine-readable declarations should match actual runtime behavior.
- Custom extensions must remain stable within the current major line unless a
  change is explicitly documented as breaking.
- Shared metadata and wire contracts should not drift between Agent Card,
  OpenAPI, and runtime behavior.

More detail: [Compatibility Guide](docs/compatibility.md)

## Security and Secrets

- Never commit bearer tokens, provider keys, or `.env` contents.
- Do not add logs that expose raw credentials or private payloads.
- Deployment, authentication, or secret-handling changes must update the
  relevant documentation.

## Reviews

Good PRs in this repository are usually:

- small enough to review quickly
- explicit about contract changes
- backed by tests when runtime behavior changes
- clear about residual risk when the work is intentionally partial
