# Compatibility Guide

This document explains the compatibility promises this repository currently
tries to uphold for open-source users, A2A consumers, and coding-agent
integrators.

## Runtime Support

- Python versions: 3.11, 3.12, 3.13
- A2A SDK line: `0.3.x`
- A2A protocol version advertised by default: `0.3.0`

The repository pins the SDK version in `pyproject.toml` and validates the
published CLI build in CI. Upgrade the SDK deliberately rather than relying on
floating dependency resolution.

## Contract Honesty

Machine-readable discovery surfaces must reflect actual runtime behavior:

- Agent Card
- OpenAPI metadata
- JSON-RPC wire contract
- compatibility profile

If runtime support is not implemented, do not expose it as a supported machine-
readable capability.

## Normative Sources

When documentation or reference material disagrees, treat these as normative in
this order:

- runtime behavior validated by tests
- machine-readable discovery output such as Agent Card and OpenAPI metadata
- repository-owned docs in `README.md`, `docs/`, and `CONTRIBUTING.md`

Maintainer-local upstream Codex snapshots generated via
`scripts/sync_codex_docs.sh` are optional reference inputs for comparison and
protocol context. They do not override this repository's declared service
contract.

## Compatibility-Sensitive Surface

This repository still ships as an alpha project. Within that alpha line, these
declared surfaces should not drift silently:

- core A2A send / stream / task methods
- shared session-binding metadata
- shared streaming metadata
- declared custom JSON-RPC extension methods
- unsupported-method error shape

Changes to those surfaces should be treated as compatibility-sensitive and
should include corresponding test updates.

Service-level behavior layered on top of those core methods should also be
declared explicitly when this repository depends on it for interoperability.
Current example: terminal `tasks/resubscribe` replay-once behavior is published
as a service-level contract, not as a claim about generic A2A runtime
semantics.

## Deployment Profile

The current service profile is intentionally:

- single-tenant
- shared-workspace
- `tenant_isolation=none`

One deployed instance should be treated as a single-tenant trust boundary, not
as a secure multi-tenant runtime boundary.

The compatibility surface distinguishes between:

- a stable deployment profile
- runtime features such as directory binding policy, session shell availability,
  interrupt TTL, and health endpoint exposure

Execution-environment boundary fields are also published through the runtime
profile when configured. Those fields are declarative deployment metadata, not
promises that every temporary approval, sandbox escalation, or host-side change
will be reflected live per request.

## Extension Stability

- Shared metadata and extension contracts should stay synchronized across Agent
  Card, OpenAPI, and runtime behavior.
- Product-specific extensions should remain stable within the current major
  line unless explicitly documented otherwise.
- Deployment-conditional methods must be declared as conditional rather than
  silently disappearing.

## Extension Taxonomy

This repository distinguishes between three layers:

- core A2A surface
  - standard send / stream / task methods
- shared extensions
  - repo-family conventions such as session binding, stream hints, and
    interrupt callbacks
- Codex-specific extensions
  - `codex.*` JSON-RPC methods and `metadata.codex.directory`

Important note:

- `urn:a2a:*` extension URIs used here should be read as shared conventions in
  this repository family.
- They are not a claim that those extensions are part of the A2A core baseline.

## Non-Goals

This repository does not currently promise:

- multi-tenant workspace isolation inside one instance
- OAuth2 runtime token verification
- a generic metrics export protocol such as Prometheus or OpenTelemetry

Those areas may evolve later, but they should not be implied by current
machine-readable discovery output.
