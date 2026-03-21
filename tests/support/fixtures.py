from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from codex_a2a_server.upstream.client import CodexClient
from tests.support.settings import make_settings

_TESTS_DIR = Path(__file__).resolve().parent.parent
_FIXTURES_DIR = _TESTS_DIR / "fixtures"


def load_json_fixture(*relative_parts: str) -> Any:
    fixture_path = _FIXTURES_DIR.joinpath(*relative_parts)
    return json.loads(fixture_path.read_text(encoding="utf-8"))


async def replay_codex_notification_fixture(
    *relative_parts: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fixture = load_json_fixture(*relative_parts)
    client = CodexClient(make_settings(a2a_bearer_token="test-token", codex_timeout=1.0))
    events: list[dict[str, Any]] = []

    async def fake_enqueue(event: dict) -> None:
        events.append(event)

    client._enqueue_stream_event = fake_enqueue
    for notification in fixture["notifications"]:
        await client._handle_notification(notification)
    return fixture, events


async def replay_codex_jsonrpc_line_fixture(
    *relative_parts: str,
    prefix_lines: list[bytes] | None = None,
    suffix_lines: list[bytes] | None = None,
    chunk_sizes: tuple[int, ...] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fixture = load_json_fixture(*relative_parts)
    client = CodexClient(make_settings(a2a_bearer_token="test-token", codex_timeout=1.0))
    events: list[dict[str, Any]] = []

    async def fake_enqueue(event: dict) -> None:
        events.append(event)

    client._enqueue_stream_event = fake_enqueue

    raw_lines: list[bytes] = []
    if prefix_lines:
        raw_lines.extend(prefix_lines)
    raw_lines.extend(
        (json.dumps(notification, ensure_ascii=False) + "\n").encode("utf-8")
        for notification in fixture["notifications"]
    )
    if suffix_lines:
        raw_lines.extend(suffix_lines)

    encoded = b"".join(raw_lines)
    if chunk_sizes:
        chunks: list[bytes] = []
        cursor = 0
        for size in chunk_sizes:
            if cursor >= len(encoded):
                break
            chunks.append(encoded[cursor : cursor + size])
            cursor += size
        if cursor < len(encoded):
            chunks.append(encoded[cursor:])
    else:
        chunks = [encoded]

    class _ChunkedStream:
        def __init__(self, items: list[bytes]) -> None:
            self._items = list(items)

        async def read(self, _size: int) -> bytes:
            if not self._items:
                return b""
            return self._items.pop(0)

    process = MagicMock()
    process.stdout = _ChunkedStream(chunks)
    client._process = process

    await client._read_stdout_loop()
    return fixture, events
