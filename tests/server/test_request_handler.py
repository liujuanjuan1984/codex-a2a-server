from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskIdParams,
    TaskState,
    TaskStatus,
    TextPart,
)

from codex_a2a_server.server.request_handler import CodexRequestHandler


def _make_message_send_params() -> MessageSendParams:
    return MessageSendParams(
        message=Message(
            message_id="m-1",
            role=Role.user,
            parts=[Part(root=TextPart(text="hello"))],
        )
    )


@pytest.mark.asyncio
async def test_cancel_is_idempotent_for_already_canceled_task() -> None:
    task_store = InMemoryTaskStore()
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.canceled),
    )
    await task_store.save(task)

    handler = CodexRequestHandler(agent_executor=MagicMock(), task_store=task_store)

    result = await handler.on_cancel_task(TaskIdParams(id="task-1"))

    assert result == task


@pytest.mark.asyncio
async def test_resubscribe_replays_terminal_task_once() -> None:
    task_store = InMemoryTaskStore()
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.completed),
    )
    await task_store.save(task)

    handler = CodexRequestHandler(agent_executor=MagicMock(), task_store=task_store)

    events = [event async for event in handler.on_resubscribe_to_task(TaskIdParams(id="task-1"))]

    assert events == [task]


@pytest.mark.asyncio
async def test_stream_disconnect_cancels_producer() -> None:
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

    await stream.aclose()
    await asyncio.sleep(0)

    assert handler._producer_task.cancelled()
    handler._queue.close.assert_awaited_once_with(immediate=True)


@pytest.mark.asyncio
async def test_stream_disconnect_does_not_leave_unretrieved_loop_exceptions() -> None:
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
    loop = asyncio.get_running_loop()
    loop_exceptions: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_exceptions.append(context))

    try:
        stream = handler.on_message_send_stream(_make_message_send_params())
        first_event = await stream.__anext__()
        assert isinstance(first_event, Task)

        await stream.aclose()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert handler._producer_task.cancelled()
    assert loop_exceptions == []
    assert handler._background_tasks == set()
