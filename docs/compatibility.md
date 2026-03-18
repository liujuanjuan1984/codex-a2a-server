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

## Stable Surface

The current major line treats these areas as stable service-facing contracts:

- core A2A send / stream / task methods
- shared session-binding metadata
- shared streaming metadata
- declared custom JSON-RPC extension methods
- unsupported-method error shape

Changes to those surfaces should be treated as compatibility-sensitive and
should include corresponding test updates.

## Extension Stability

- Shared metadata and extension contracts should stay synchronized across Agent
  Card, OpenAPI, and runtime behavior.
- Product-specific extensions should remain stable within the current major
  line unless explicitly documented otherwise.
- Deployment-conditional methods must be declared as conditional rather than
  silently disappearing.

## Non-Goals

This repository does not currently promise:

- multi-tenant workspace isolation inside one instance
- OAuth2 runtime token verification
- a generic metrics export protocol such as Prometheus or OpenTelemetry

Those areas may evolve later, but they should not be implied by current
machine-readable discovery output.
