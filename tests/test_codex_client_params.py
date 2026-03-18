import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest

from codex_a2a_server.codex_client import (
    CodexClient,
    InterruptRequestBinding,
    _PendingInterruptRequest,
)
from tests.helpers import (
    make_settings,
    replay_codex_jsonrpc_line_fixture,
    replay_codex_notification_fixture,
)


@pytest.mark.asyncio
async def test_list_calls_use_expected_rpc_params() -> None:
    client = CodexClient(
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
async def test_session_shell_uses_command_exec_without_thread_context() -> None:
    client = CodexClient(
        make_settings(
            a2a_bearer_token="t-1",
            codex_directory="/safe",
            codex_timeout=1.0,
        )
    )

    seen: list[tuple[str, dict | None]] = []

    async def fake_rpc_request(method: str, params: dict | None = None):
        seen.append((method, params))
        return {"stdout": "/safe\n", "stderr": "", "exitCode": 0}

    client._rpc_request = fake_rpc_request  # type: ignore[method-assign]

    result = await client.session_shell("thr-1", {"command": "pwd"})

    assert seen == [("command/exec", {"command": ["pwd"], "cwd": "/safe"})]
    assert result["info"]["id"].startswith("shell:thr-1:")
    assert result["parts"][0]["text"] == "exit_code: 0\nstdout:\n/safe"


@pytest.mark.asyncio
async def test_permission_reply_maps_to_codex_decision() -> None:
    client = CodexClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))
    client._pending_server_requests["100"] = _PendingInterruptRequest(
        binding=InterruptRequestBinding(
            request_id="100",
            interrupt_type="permission",
            session_id="thr-1",
            created_at=time.monotonic(),
            provider_method="item/commandExecution/requestApproval",
        ),
        rpc_request_id=100,
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
    client = CodexClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))
    client._pending_server_requests["200"] = _PendingInterruptRequest(
        binding=InterruptRequestBinding(
            request_id="200",
            interrupt_type="question",
            session_id="thr-2",
            created_at=time.monotonic(),
            provider_method="item/tool/requestUserInput",
        ),
        rpc_request_id=200,
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


def test_interrupt_request_status_uses_configured_ttl(monkeypatch) -> None:
    client = CodexClient(
        make_settings(
            a2a_bearer_token="t-1",
            codex_timeout=1.0,
            a2a_interrupt_request_ttl_seconds=5,
        )
    )
    client._pending_server_requests["req-1"] = _PendingInterruptRequest(
        binding=InterruptRequestBinding(
            request_id="req-1",
            interrupt_type="permission",
            session_id="thr-1",
            created_at=10.0,
            provider_method="item/commandExecution/requestApproval",
        ),
        rpc_request_id=1,
        params={"threadId": "thr-1"},
    )

    monkeypatch.setattr("codex_a2a_server.codex_client.time.monotonic", lambda: 14.0)
    assert client.resolve_interrupt_request("req-1")[0] == "active"

    monkeypatch.setattr("codex_a2a_server.codex_client.time.monotonic", lambda: 15.0)
    assert client.resolve_interrupt_request("req-1")[0] == "expired"
    assert client.resolve_interrupt_request("req-1")[0] == "missing"
    assert "200" not in client._pending_server_requests


@pytest.mark.asyncio
async def test_stream_events_broadcasts_to_all_consumers() -> None:
    client = CodexClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))

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
async def test_handle_notification_normalizes_tool_output_delta_payload() -> None:
    client = CodexClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))
    events: list[dict] = []

    async def fake_enqueue(event: dict) -> None:
        events.append(event)

    client._enqueue_stream_event = fake_enqueue  # type: ignore[method-assign]

    await client._handle_notification(
        {
            "method": "item/commandExecution/outputDelta",
            "params": {
                "threadId": "thr-1",
                "itemId": "msg-1",
                "callID": "call-1",
                "tool": "bash",
                "state": {"status": "running"},
                "delta": "Passed\n",
            },
        }
    )

    assert len(events) == 1
    event = events[0]
    assert event["type"] == "message.part.updated"
    assert event["properties"]["part"] == {
        "sessionID": "thr-1",
        "messageID": "msg-1",
        "id": "msg-1",
        "type": "tool_call",
        "role": "assistant",
        "callID": "call-1",
        "tool": "bash",
        "state": {"status": "running"},
        "sourceMethod": "commandExecution",
    }
    assert event["properties"]["delta"] == {
        "kind": "output_delta",
        "source_method": "commandExecution",
        "call_id": "call-1",
        "tool": "bash",
        "status": "running",
        "output_delta": "Passed\n",
    }


@pytest.mark.asyncio
async def test_handle_notification_normalizes_file_change_output_delta_payload() -> None:
    client = CodexClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))
    events: list[dict] = []

    async def fake_enqueue(event: dict) -> None:
        events.append(event)

    client._enqueue_stream_event = fake_enqueue  # type: ignore[method-assign]

    await client._handle_notification(
        {
            "method": "item/fileChange/outputDelta",
            "params": {
                "threadId": "thr-1",
                "callID": "call-file-1",
                "tool": "apply_patch",
                "delta": "Updated src/app.py\n",
            },
        }
    )

    assert len(events) == 1
    event = events[0]
    assert event["type"] == "message.part.updated"
    assert event["properties"]["part"] == {
        "sessionID": "thr-1",
        "id": "call-file-1",
        "type": "tool_call",
        "role": "assistant",
        "callID": "call-file-1",
        "tool": "apply_patch",
        "sourceMethod": "fileChange",
    }
    assert event["properties"]["delta"] == {
        "kind": "output_delta",
        "source_method": "fileChange",
        "call_id": "call-file-1",
        "tool": "apply_patch",
        "output_delta": "Updated src/app.py\n",
    }


@pytest.mark.asyncio
async def test_handle_notification_normalizes_command_execution_started_state() -> None:
    client = CodexClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))
    events: list[dict] = []

    async def fake_enqueue(event: dict) -> None:
        events.append(event)

    client._enqueue_stream_event = fake_enqueue  # type: ignore[method-assign]

    await client._handle_notification(
        {
            "method": "item/started",
            "params": {
                "threadId": "thr-1",
                "item": {
                    "type": "commandExecution",
                    "id": "call-1",
                    "status": "inProgress",
                    "command": "/bin/bash -lc pytest",
                    "cwd": "/workspace",
                },
            },
        }
    )

    assert len(events) == 1
    event = events[0]
    assert event["properties"]["part"] == {
        "sessionID": "thr-1",
        "messageID": "call-1",
        "id": "call-1",
        "type": "tool_call",
        "role": "assistant",
        "callID": "call-1",
        "sourceMethod": "commandExecution",
        "state": {
            "status": "running",
            "input": {
                "command": "/bin/bash -lc pytest",
                "cwd": "/workspace",
            },
        },
    }
    assert event["properties"]["delta"] == {
        "kind": "state",
        "source_method": "commandExecution",
        "call_id": "call-1",
        "status": "running",
        "input": {
            "command": "/bin/bash -lc pytest",
            "cwd": "/workspace",
        },
    }


@pytest.mark.asyncio
async def test_handle_notification_normalizes_file_change_completed_state() -> None:
    client = CodexClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))
    events: list[dict] = []

    async def fake_enqueue(event: dict) -> None:
        events.append(event)

    client._enqueue_stream_event = fake_enqueue  # type: ignore[method-assign]

    await client._handle_notification(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thr-1",
                "item": {
                    "type": "fileChange",
                    "id": "call-file-1",
                    "status": "completed",
                    "changes": [
                        {"path": "/workspace/src/app.py", "kind": {"type": "edit"}},
                    ],
                },
            },
        }
    )

    assert len(events) == 1
    event = events[0]
    assert event["properties"]["part"] == {
        "sessionID": "thr-1",
        "messageID": "call-file-1",
        "id": "call-file-1",
        "type": "tool_call",
        "role": "assistant",
        "callID": "call-file-1",
        "sourceMethod": "fileChange",
        "state": {
            "status": "completed",
            "input": {
                "paths": ["/workspace/src/app.py"],
                "change_count": 1,
            },
        },
    }
    assert event["properties"]["delta"] == {
        "kind": "state",
        "source_method": "fileChange",
        "call_id": "call-file-1",
        "status": "completed",
        "input": {
            "paths": ["/workspace/src/app.py"],
            "change_count": 1,
        },
    }


@pytest.mark.asyncio
async def test_handle_notification_replays_real_command_execution_fixture() -> None:
    fixture, events = await replay_codex_notification_fixture(
        "codex_app_server",
        "command_execution_output_delta.json",
    )
    tool_events = [event for event in events if event["properties"]["part"]["type"] == "tool_call"]
    expected_command = (
        '/bin/bash -lc "python3 -c \\"import sys,time; '
        "[print(f'chunk-{i}', flush=True) or time.sleep(0.2) for i in range(3)]"
        '\\""'
    )

    assert fixture["response_text"] == "DONE"
    assert [event["type"] for event in tool_events] == [
        "message.part.updated",
        "message.part.updated",
        "message.part.updated",
        "message.part.updated",
    ]
    assert tool_events[0]["properties"]["part"] == {
        "sessionID": "thr-fixture-command",
        "messageID": "call-fixture-command",
        "id": "call-fixture-command",
        "type": "tool_call",
        "role": "assistant",
        "callID": "call-fixture-command",
        "sourceMethod": "commandExecution",
        "state": {
            "status": "running",
            "input": {
                "command": expected_command,
                "cwd": "/tmp/codex-a2a-command-fixture",
            },
        },
    }
    assert tool_events[0]["properties"]["delta"]["kind"] == "state"
    assert [event["properties"]["delta"]["output_delta"] for event in tool_events[1:3]] == [
        "chunk-1\n",
        "chunk-2\n",
    ]
    assert tool_events[3]["properties"]["delta"] == {
        "kind": "state",
        "source_method": "commandExecution",
        "call_id": "call-fixture-command",
        "status": "completed",
        "input": {
            "command": expected_command,
            "cwd": "/tmp/codex-a2a-command-fixture",
        },
        "output": {
            "text": "chunk-1\nchunk-2\n",
            "exit_code": 0,
            "duration_ms": 487,
        },
    }


@pytest.mark.asyncio
async def test_read_stdout_loop_replays_real_command_execution_jsonrpc_lines() -> None:
    fixture, events = await replay_codex_jsonrpc_line_fixture(
        "codex_app_server",
        "command_execution_output_delta.json",
        chunk_sizes=(97, 211, 503),
    )
    tool_events = [event for event in events if event["properties"]["part"]["type"] == "tool_call"]

    assert fixture["response_text"] == "DONE"
    assert [event["type"] for event in tool_events] == [
        "message.part.updated",
        "message.part.updated",
        "message.part.updated",
        "message.part.updated",
    ]
    assert [event["properties"]["delta"]["kind"] for event in tool_events] == [
        "state",
        "output_delta",
        "output_delta",
        "state",
    ]
    assert tool_events[-1]["properties"]["delta"]["status"] == "completed"


@pytest.mark.asyncio
async def test_handle_notification_replays_real_file_change_fixture() -> None:
    fixture, events = await replay_codex_notification_fixture(
        "codex_app_server",
        "file_change_output_delta.json",
    )
    tool_events = [event for event in events if event["properties"]["part"]["type"] == "tool_call"]

    assert fixture["response_text"] == "DONE"
    assert [event["type"] for event in tool_events] == [
        "message.part.updated",
        "message.part.updated",
        "message.part.updated",
    ]
    assert tool_events[0]["properties"]["part"] == {
        "sessionID": "thr-fixture-file-change",
        "messageID": "call-fixture-file-change",
        "id": "call-fixture-file-change",
        "type": "tool_call",
        "role": "assistant",
        "callID": "call-fixture-file-change",
        "sourceMethod": "fileChange",
        "state": {
            "status": "running",
            "input": {
                "paths": ["/tmp/codex-a2a-file-change-fixture/fixture-from-codex.txt"],
                "change_count": 1,
            },
        },
    }
    assert tool_events[1]["properties"]["delta"] == {
        "kind": "output_delta",
        "source_method": "fileChange",
        "call_id": "call-fixture-file-change",
        "output_delta": "Success. Updated the following files:\nA fixture-from-codex.txt\n",
    }
    assert tool_events[2]["properties"]["delta"] == {
        "kind": "state",
        "source_method": "fileChange",
        "call_id": "call-fixture-file-change",
        "status": "completed",
        "input": {
            "paths": ["/tmp/codex-a2a-file-change-fixture/fixture-from-codex.txt"],
            "change_count": 1,
        },
    }


@pytest.mark.asyncio
async def test_read_stdout_loop_replays_real_file_change_jsonrpc_lines() -> None:
    fixture, events = await replay_codex_jsonrpc_line_fixture(
        "codex_app_server",
        "file_change_output_delta.json",
        chunk_sizes=(41, 89, 233),
    )
    tool_events = [event for event in events if event["properties"]["part"]["type"] == "tool_call"]

    assert fixture["response_text"] == "DONE"
    assert [event["type"] for event in tool_events] == [
        "message.part.updated",
        "message.part.updated",
        "message.part.updated",
    ]
    assert tool_events[1]["properties"]["delta"]["kind"] == "output_delta"
    assert tool_events[2]["properties"]["delta"]["status"] == "completed"


@pytest.mark.asyncio
async def test_read_stdout_loop_drops_invalid_and_non_object_json_lines(caplog) -> None:
    with caplog.at_level("DEBUG", logger="codex_a2a_server.codex_client"):
        fixture, events = await replay_codex_jsonrpc_line_fixture(
            "codex_app_server",
            "command_execution_output_delta.json",
            prefix_lines=[
                b'{"method":"turn/started","params":\n',
                b"42\n",
                b"[1,2,3]\n",
            ],
            chunk_sizes=(19, 37, 211),
        )

    assert fixture["response_text"] == "DONE"
    assert any(event["type"] == "message.part.updated" for event in events)
    assert "drop non-json line from codex app-server" in caplog.text
    assert "drop non-object jsonrpc payload from codex app-server: int" in caplog.text
    assert "drop non-object jsonrpc payload from codex app-server: list" in caplog.text


@pytest.mark.asyncio
async def test_send_message_timeout_override_none_disables_wait_timeout() -> None:
    client = CodexClient(make_settings(a2a_bearer_token="t-1", codex_timeout=0.01))

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
    client = CodexClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))
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


@pytest.mark.asyncio
async def test_ensure_started_passes_reasoning_effort_override_to_codex_cli() -> None:
    client = CodexClient(
        make_settings(
            a2a_bearer_token="t-1",
            codex_timeout=1.0,
            codex_cli_bin="codex-custom",
            codex_model_reasoning_effort="high",
        )
    )

    captured: list[tuple] = []

    class _DummyStdin:
        def write(self, _data: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

    dummy_process = MagicMock()
    dummy_process.stdin = _DummyStdin()
    dummy_process.stdout = object()
    dummy_process.stderr = object()
    dummy_process.returncode = 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured.append((args, kwargs))
        return dummy_process

    async def fake_rpc_request(
        method: str, params: dict | None = None, *, _skip_ensure: bool = False
    ):
        assert method == "initialize"
        assert _skip_ensure is True
        return {}

    async def fake_send_json(payload: dict) -> None:
        assert payload == {"method": "initialized", "params": {}}

    async def fake_stdout_loop() -> None:
        return None

    async def fake_stderr_loop() -> None:
        return None

    client._rpc_request = fake_rpc_request  # type: ignore[method-assign]
    client._send_json_message = fake_send_json  # type: ignore[method-assign]
    client._read_stdout_loop = fake_stdout_loop  # type: ignore[method-assign]
    client._read_stderr_loop = fake_stderr_loop  # type: ignore[method-assign]

    original = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = fake_create_subprocess_exec  # type: ignore[assignment]
    try:
        await client._ensure_started()
    finally:
        asyncio.create_subprocess_exec = original  # type: ignore[assignment]
        await client.close()

    assert captured
    args, _kwargs = captured[0]
    assert args[:4] == ("codex-custom", "-c", 'model_reasoning_effort="high"', "app-server")


@pytest.mark.asyncio
async def test_read_stdout_loop_handles_very_long_json_line() -> None:
    client = CodexClient(make_settings(a2a_bearer_token="t-1", codex_timeout=1.0))
    payload = {"method": "event/test", "params": {"blob": "x" * 200_000}}
    encoded = (json.dumps(payload) + "\n").encode("utf-8")

    class _ChunkedStream:
        def __init__(self, chunks: list[bytes]) -> None:
            self._chunks = list(chunks)

        async def read(self, _size: int) -> bytes:
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

    process = MagicMock()
    process.stdout = _ChunkedStream(
        [
            encoded[:70_000],
            encoded[70_000:140_000],
            encoded[140_000:],
        ]
    )
    client._process = process

    seen: list[dict] = []

    async def fake_dispatch(message: dict[str, object]) -> None:
        seen.append(message)

    client._dispatch_message = fake_dispatch  # type: ignore[method-assign]

    await client._read_stdout_loop()

    assert len(seen) == 1
    assert seen[0]["method"] == "event/test"
    assert seen[0]["params"]["blob"] == "x" * 200_000
