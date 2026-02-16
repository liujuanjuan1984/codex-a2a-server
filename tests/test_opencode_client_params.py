import asyncio

import pytest

from codex_a2a_serve.codex_client import OpencodeClient, _PendingServerRequest
from tests.helpers import make_settings


@pytest.mark.asyncio
async def test_list_calls_use_expected_rpc_params() -> None:
    client = OpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            codex_directory="/safe",
            codex_timeout=1.0,
        )
    )

    seen: list[tuple[str, dict | None]] = []

    async def fake_rpc_request(method: str, params: dict | None = None):
        seen.append((method, params))
        if method == "thread/list":
            return {"data": [{"id": "thr-1", "preview": "hello"}]}
        if method == "thread/read":
            return {"thread": {"turns": []}}
        return {}

    client._rpc_request = fake_rpc_request  # type: ignore[method-assign]

    sessions = await client.list_sessions(params={"directory": "/evil", "limit": 1, "roots": True})
    assert sessions == [
        {"id": "thr-1", "title": "hello", "raw": {"id": "thr-1", "preview": "hello"}}
    ]

    messages = await client.list_messages("thr-1", params={"directory": "/evil", "limit": 10})
    assert messages == []

    assert seen[0] == ("thread/list", {"limit": 1})
    assert seen[1] == ("thread/read", {"threadId": "thr-1", "includeTurns": True})


@pytest.mark.asyncio
async def test_permission_reply_maps_to_codex_decision() -> None:
    client = OpencodeClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))
    client._pending_server_requests["100"] = _PendingServerRequest(
        method="item/commandExecution/requestApproval",
        request_id=100,
        params={"threadId": "thr-1"},
    )

    sent: list[dict] = []
    events: list[dict] = []

    async def fake_send_json(payload: dict) -> None:
        sent.append(payload)

    async def fake_enqueue(event: dict) -> None:
        events.append(event)

    client._send_json_message = fake_send_json  # type: ignore[method-assign]
    client._enqueue_stream_event = fake_enqueue  # type: ignore[method-assign]

    ok = await client.permission_reply("100", reply="always")
    assert ok is True
    assert sent == [{"id": 100, "result": {"decision": "acceptForSession"}}]
    assert events[-1]["type"] == "permission.replied"
    assert events[-1]["properties"]["id"] == "100"
    assert events[-1]["properties"]["requestID"] == "100"
    assert "100" not in client._pending_server_requests


@pytest.mark.asyncio
async def test_question_reply_builds_answer_map() -> None:
    client = OpencodeClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))
    client._pending_server_requests["200"] = _PendingServerRequest(
        method="item/tool/requestUserInput",
        request_id=200,
        params={
            "threadId": "thr-2",
            "questions": [
                {"id": "q1", "question": "Q1"},
                {"id": "q2", "question": "Q2"},
            ],
        },
    )

    sent: list[dict] = []

    async def fake_send_json(payload: dict) -> None:
        sent.append(payload)

    async def fake_enqueue(_event: dict) -> None:
        return None

    client._send_json_message = fake_send_json  # type: ignore[method-assign]
    client._enqueue_stream_event = fake_enqueue  # type: ignore[method-assign]

    ok = await client.question_reply("200", answers=[["A"], ["B", "C"]])
    assert ok is True
    assert sent == [
        {
            "id": 200,
            "result": {
                "answers": {
                    "q1": {"answers": ["A"]},
                    "q2": {"answers": ["B", "C"]},
                }
            },
        }
    ]
    assert "200" not in client._pending_server_requests


@pytest.mark.asyncio
async def test_stream_events_broadcasts_to_all_consumers() -> None:
    client = OpencodeClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))

    async def fake_ensure_started() -> None:
        return None

    client._ensure_started = fake_ensure_started  # type: ignore[method-assign]

    stop_1 = asyncio.Event()
    stop_2 = asyncio.Event()
    seen_1: list[dict] = []
    seen_2: list[dict] = []

    async def consume(stop_event: asyncio.Event, out: list[dict]) -> None:
        async for event in client.stream_events(stop_event=stop_event):
            out.append(event)
            stop_event.set()

    task_1 = asyncio.create_task(consume(stop_1, seen_1))
    task_2 = asyncio.create_task(consume(stop_2, seen_2))

    for _ in range(20):
        if len(client._event_subscribers) == 2:
            break
        await asyncio.sleep(0)

    payload = {"type": "message.part.updated", "properties": {"sessionID": "thr-1"}}
    await client._enqueue_stream_event(payload)
    await asyncio.wait_for(asyncio.gather(task_1, task_2), timeout=1.0)

    assert seen_1 == [payload]
    assert seen_2 == [payload]


@pytest.mark.asyncio
async def test_send_message_timeout_override_none_disables_wait_timeout() -> None:
    client = OpencodeClient(make_settings(a2a_bearer_token="t-1", codex_timeout=0.01))

    async def fake_rpc_request(_method: str, _params: dict | None = None):
        return {"turn": {"id": "turn-1"}}

    client._rpc_request = fake_rpc_request  # type: ignore[method-assign]
    tracker = client._get_or_create_tracker("thr-1", "turn-1")

    async def finish_turn() -> None:
        await asyncio.sleep(0.05)
        tracker.text_chunks.append("done")
        tracker.completed.set()

    finisher = asyncio.create_task(finish_turn())
    message = await asyncio.wait_for(
        client.send_message("thr-1", "hello", timeout_override=None),
        timeout=1.0,
    )
    await finisher

    assert message.text == "done"
    assert ("thr-1", "turn-1") not in client._turn_trackers


@pytest.mark.asyncio
async def test_unsupported_server_request_returns_jsonrpc_error() -> None:
    client = OpencodeClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))
    sent: list[dict] = []

    async def fake_send_json(payload: dict) -> None:
        sent.append(payload)

    client._send_json_message = fake_send_json  # type: ignore[method-assign]

    await client._handle_server_request({"id": 300, "method": "item/tool/call", "params": {}})

    assert sent == [
        {
            "id": 300,
            "error": {
                "code": -32601,
                "message": "Unsupported server request method: item/tool/call",
            },
        }
    ]
