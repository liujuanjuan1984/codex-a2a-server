from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import Task, TaskState, TaskStatus

from codex_a2a_server.metrics import (
    A2A_STREAM_ACTIVE,
    A2A_STREAM_REQUESTS_TOTAL,
    CODEX_STREAM_RETRIES_TOTAL,
    INTERRUPT_REQUESTS_TOTAL,
    INTERRUPT_RESOLVED_TOTAL,
    TOOL_CALL_CHUNKS_EMITTED_TOTAL,
    reset_metrics,
    snapshot_metrics,
)
from codex_a2a_server.request_handler import CodexRequestHandler
from codex_a2a_server.stream_state import StreamOutputState
from codex_a2a_server.streaming import consume_codex_stream
from tests.helpers import DummyEventQueue
from tests.test_request_handler import _make_message_send_params


async def _empty_async_stream() -> None:
    if asyncio.current_task() is None:
        yield b""


@pytest.fixture(autouse=True)
def reset_metrics_state() -> Iterator[None]:
    reset_metrics()
    yield
    reset_metrics()


@pytest.mark.asyncio
async def test_stream_request_metrics_track_total_and_active() -> None:
    class _FakeAggregator:
        async def consume_and_emit(self, _consumer):
            task = Task(
                id="task-1",
                context_id="ctx-1",
                status=TaskStatus(state=TaskState.working),
            )
            yield task
            await asyncio.sleep(10)

    class _TestHandler(CodexRequestHandler):
        async def _setup_message_execution(self, params, context=None):  # noqa: ANN001
            del params, context
            queue = AsyncMock()
            producer_task = asyncio.create_task(asyncio.sleep(10))
            self._producer_task = producer_task
            self._queue = queue
            return MagicMock(), "task-1", queue, _FakeAggregator(), producer_task

        async def _cleanup_producer(self, producer_task, task_id):  # noqa: ANN001
            del task_id
            try:
                await producer_task
            except asyncio.CancelledError:
                pass

    handler = _TestHandler(agent_executor=MagicMock(), task_store=InMemoryTaskStore())

    stream = handler.on_message_send_stream(_make_message_send_params())
    first_event = await stream.__anext__()
    assert isinstance(first_event, Task)
    assert snapshot_metrics()["counters"][A2A_STREAM_REQUESTS_TOTAL] == 1
    assert snapshot_metrics()["gauges"][A2A_STREAM_ACTIVE] == 1

    await stream.aclose()
    await asyncio.sleep(0)

    assert snapshot_metrics()["counters"][A2A_STREAM_REQUESTS_TOTAL] == 1
    assert snapshot_metrics()["gauges"][A2A_STREAM_ACTIVE] == 0


@pytest.mark.asyncio
async def test_streaming_metrics_capture_tool_call_and_interrupt_events() -> None:
    class _Client:
        async def stream_events(self, stop_event=None, *, directory=None):  # noqa: ANN001
            del stop_event, directory
            yield {
                "type": "permission.asked",
                "properties": {
                    "id": "perm-1",
                    "sessionID": "ses-1",
                    "permission": "read",
                    "patterns": ["/tmp/secret"],
                    "always": [],
                },
            }
            yield {
                "type": "permission.replied",
                "properties": {
                    "id": "perm-1",
                    "sessionID": "ses-1",
                },
            }
            yield {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "id": "part-tool-1",
                        "sessionID": "ses-1",
                        "messageID": "msg-1",
                        "type": "tool",
                        "role": "assistant",
                        "callID": "call-1",
                        "tool": "bash",
                        "state": {"status": "running"},
                    },
                    "delta": "",
                },
            }

    await consume_codex_stream(
        client=_Client(),
        session_id="ses-1",
        task_id="task-1",
        context_id="ctx-1",
        artifact_id="task-1:stream",
        stream_state=StreamOutputState(
            user_text="hello",
            stable_message_id="task-1:ctx-1:assistant",
            event_id_namespace="task-1:ctx-1:task-1:stream",
        ),
        event_queue=DummyEventQueue(),
        stop_event=asyncio.Event(),
        completion_event=asyncio.Event(),
    )

    snapshot = snapshot_metrics()
    assert snapshot["counters"][INTERRUPT_REQUESTS_TOTAL] == 1
    assert snapshot["counters"][INTERRUPT_RESOLVED_TOTAL] == 1
    assert snapshot["counters"][TOOL_CALL_CHUNKS_EMITTED_TOTAL] == 1


@pytest.mark.asyncio
async def test_streaming_retry_metric_increments_once_per_retry(monkeypatch) -> None:
    class _FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        async def stream_events(self, stop_event=None, *, directory=None):  # noqa: ANN001
            del stop_event, directory
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            async for event in _empty_async_stream():
                yield event

    async def _fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("codex_a2a_server.streaming.asyncio.sleep", _fast_sleep)

    await consume_codex_stream(
        client=_FlakyClient(),
        session_id="ses-1",
        task_id="task-1",
        context_id="ctx-1",
        artifact_id="task-1:stream",
        stream_state=StreamOutputState(
            user_text="hello",
            stable_message_id="task-1:ctx-1:assistant",
            event_id_namespace="task-1:ctx-1:task-1:stream",
        ),
        event_queue=DummyEventQueue(),
        stop_event=asyncio.Event(),
        completion_event=asyncio.Event(),
    )

    assert snapshot_metrics()["counters"][CODEX_STREAM_RETRIES_TOTAL] == 1
