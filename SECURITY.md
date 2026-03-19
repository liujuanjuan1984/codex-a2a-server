# Security Policy

## Scope

This repository is an adapter layer that exposes Codex through A2A HTTP+JSON and
JSON-RPC interfaces. It adds authentication, task/session contracts, and
streaming behavior, but it does not fully isolate upstream model credentials
from Codex runtime behavior.

The current deployment model is a single-tenant trust boundary by design.

## Security Boundary

- `A2A_BEARER_TOKEN` protects access to the A2A surface, but it is not a
  tenant-isolation boundary inside one deployed instance.
- Within one `codex-a2a-server` instance, consumers share the same underlying
  Codex workspace/environment by default.
- LLM provider keys are consumed by the `codex` process. Prompt injection or
  indirect exfiltration attempts may still expose sensitive values.
- Payload logging is opt-in. When `A2A_LOG_PAYLOADS=true`, this service only
  logs JSON payload previews, applies size guards, and suppresses full payload
  logging for `codex.*` JSON-RPC extension calls.

## Threat Model

This project is currently best suited for trusted or internal environments.
Important limits:

- Single-tenant trust boundary only; not a secure multi-tenant deployment profile
- No per-tenant workspace isolation inside one instance
- No hard guarantee that upstream provider keys are inaccessible to agent logic
- Bearer-token auth only by default; stronger identity propagation is still an
  incremental hardening area
- Operators remain responsible for host hardening, secret rotation, and process
  access controls

## Reporting a Vulnerability

Please avoid posting active secrets, bearer tokens, or reproduction payloads
that contain private data in public issues.

Preferred disclosure order:

1. Use GitHub private vulnerability reporting if it is available for this
   repository.
2. If private reporting is unavailable, contact the repository maintainer
   directly through GitHub before opening a public issue.
3. For low-risk hardening ideas that do not expose private data, a normal GitHub
   issue is acceptable.

## Supported Branches

Security fixes are expected to land on the active `main` branch first.
