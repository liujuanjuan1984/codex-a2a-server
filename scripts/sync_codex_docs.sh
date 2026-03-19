#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST_BASE="$ROOT_DIR/docs/vendor/codex"
TMP_DIR="$(mktemp -d /tmp/codex-upstream.XXXXXX)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "[1/4] 克隆 openai/codex ..."
git clone --depth 1 https://github.com/openai/codex.git "$TMP_DIR" >/dev/null

echo "[2/4] 同步上游 docs 与 app-server 规范 ..."
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

echo "[3/4] 同步 OpenAI Developers 快照 ..."
DEV_DOCS_DIR="$DEST_BASE/openai-dev-docs"
mkdir -p "$DEV_DOCS_DIR"
curl -fsSL https://developers.openai.com/codex/ >"$DEV_DOCS_DIR/codex-index.html"
curl -fsSL https://developers.openai.com/codex/app-server >"$DEV_DOCS_DIR/codex-app-server.html"
curl -fsSL https://developers.openai.com/codex/auth >"$DEV_DOCS_DIR/codex-auth.html"
curl -fsSL https://developers.openai.com/codex/config-advanced >"$DEV_DOCS_DIR/codex-config-advanced.html"
curl -fsSL https://developers.openai.com/codex/cli >"$DEV_DOCS_DIR/codex-cli.html"

SYNC_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
cat >"$DEV_DOCS_DIR/README.md" <<EOF
# OpenAI Developers 文档快照

- 来源站点: https://developers.openai.com/codex/
- 抓取时间 (UTC): $SYNC_TIME
- 说明: 保留原始 HTML 快照，便于离线检索和后续比对。

## 文件

- codex-index.html
- codex-app-server.html
- codex-auth.html
- codex-config-advanced.html
- codex-cli.html
EOF

UPSTREAM_COMMIT="$(git -C "$TMP_DIR" rev-parse HEAD)"
cat >"$DEST_BASE/SYNC.md" <<EOF
# Codex 文档同步记录

- 上游仓库: https://github.com/openai/codex
- 上游 commit: \`$UPSTREAM_COMMIT\`
- 同步时间 (UTC): \`$SYNC_TIME\`

## 本地目录说明

- \`repo-docs/\`: 上游 \`docs/\` 全量副本
- \`app-server/README.md\`: app-server 主说明
- \`app-server/test-client-README.md\`: app-server test client 说明
- \`app-server/protocol-README.md\`: protocol crate 说明
- \`app-server/codex_mcp_interface.md\`: MCP 与 app-server 接口说明
- \`app-server/protocol_v1.md\`: 协议 v1 说明
- \`app-server/schema-json/\`: app-server 协议 JSON Schema（含 \`codex_app_server_protocol.schemas.json\`）
EOF

echo "[4/4] 完成"
echo "文档目录: $DEST_BASE"
