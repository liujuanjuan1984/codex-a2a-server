#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST_BASE="$ROOT_DIR/vendor/codex"
TMP_DIR="$(mktemp -d /tmp/codex-upstream.XXXXXX)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "[1/4] Cloning openai/codex ..."
git clone --depth 1 https://github.com/openai/codex.git "$TMP_DIR" >/dev/null

echo "[2/4] Syncing upstream docs and app-server references ..."
mkdir -p "$DEST_BASE/repo-docs"
cp -a "$TMP_DIR/docs/." "$DEST_BASE/repo-docs/"

mkdir -p "$DEST_BASE/app-server"
cp -a "$TMP_DIR/codex-rs/app-server/README.md" "$DEST_BASE/app-server/README.md"
cp -a "$TMP_DIR/codex-rs/app-server-test-client/README.md" "$DEST_BASE/app-server/test-client-README.md"
cp -a "$TMP_DIR/codex-rs/protocol/README.md" "$DEST_BASE/app-server/protocol-README.md"
cp -a "$TMP_DIR/codex-rs/docs/codex_mcp_interface.md" "$DEST_BASE/app-server/codex_mcp_interface.md"
cp -a "$TMP_DIR/codex-rs/docs/protocol_v1.md" "$DEST_BASE/app-server/protocol_v1.md"

mkdir -p "$DEST_BASE/app-server/schema-json"
cp -a "$TMP_DIR/codex-rs/app-server-protocol/schema/json/." "$DEST_BASE/app-server/schema-json/"

echo "[3/4] Capturing OpenAI Developers snapshots ..."
DEV_DOCS_DIR="$DEST_BASE/openai-dev-docs"
mkdir -p "$DEV_DOCS_DIR"
curl -fsSL https://developers.openai.com/codex/ >"$DEV_DOCS_DIR/codex-index.html"
curl -fsSL https://developers.openai.com/codex/app-server >"$DEV_DOCS_DIR/codex-app-server.html"
curl -fsSL https://developers.openai.com/codex/auth >"$DEV_DOCS_DIR/codex-auth.html"
curl -fsSL https://developers.openai.com/codex/config-advanced >"$DEV_DOCS_DIR/codex-config-advanced.html"
curl -fsSL https://developers.openai.com/codex/cli >"$DEV_DOCS_DIR/codex-cli.html"

SYNC_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
cat >"$DEV_DOCS_DIR/README.md" <<EOF
# OpenAI Developers Snapshot

- Source: https://developers.openai.com/codex/
- Captured at (UTC): $SYNC_TIME
- Notes: Raw HTML snapshots are kept for offline lookup and later diffs.

## Files

- codex-index.html
- codex-app-server.html
- codex-auth.html
- codex-config-advanced.html
- codex-cli.html
EOF

UPSTREAM_COMMIT="$(git -C "$TMP_DIR" rev-parse HEAD)"
cat >"$DEST_BASE/SYNC.md" <<EOF
# Codex Documentation Sync Record

- Upstream repository: https://github.com/openai/codex
- Upstream commit: \`$UPSTREAM_COMMIT\`
- Synced at (UTC): \`$SYNC_TIME\`

## Local Directory Layout

- \`repo-docs/\`: full copy of upstream \`docs/\`
- \`app-server/README.md\`: primary app-server README
- \`app-server/test-client-README.md\`: app-server test client README
- \`app-server/protocol-README.md\`: protocol crate README
- \`app-server/codex_mcp_interface.md\`: MCP and app-server interface notes
- \`app-server/protocol_v1.md\`: protocol v1 reference
- \`app-server/schema-json/\`: app-server protocol JSON Schema, including \`codex_app_server_protocol.schemas.json\`
EOF

echo "[4/4] Done"
echo "Documentation directory: $DEST_BASE"
