from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress
from typing import Any

from a2a.server.events.event_queue import EventQueue
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent

from codex_a2a_server.execution.session_runtime import RunningExecutionSnapshot


async def emit_canceled_status(
    event_queue: EventQueue,
    *,
    task_id: str,
    context_id: str,
) -> None:
    await event_queue.enqueue_event(
        TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.canceled),
            final=True,
        )
    )


def prepare_cancel_waitables(
    running: RunningExecutionSnapshot,
    *,
    current_task: asyncio.Task[Any] | None,
) -> list[asyncio.Task[Any]]:
    if running.stop_event is not None:
        running.stop_event.set()

    waitables: list[asyncio.Task[Any]] = []
    if running.task and running.task is not current_task and not running.task.done():
        running.task.cancel()
        waitables.append(running.task)
    if running.inflight_create is not None:
        running.inflight_create.cancel()
        waitables.append(running.inflight_create)
    return waitables


async def await_cancel_cleanup(
    waitables: Sequence[asyncio.Task[Any]],
    *,
    task_id: str,
    context_id: str,
    cancel_abort_timeout_seconds: float,
    logger: logging.Logger,
) -> None:
    if waitables and cancel_abort_timeout_seconds > 0:
        done, pending = await asyncio.wait(
            set(waitables),
            timeout=cancel_abort_timeout_seconds,
        )
        for task in done:
            with suppress(asyncio.CancelledError, Exception):
                await task
        if pending:
            logger.warning(
                "Cancel abort timeout exceeded task_id=%s context_id=%s "
                "abort_timeout_seconds=%.3f pending_tasks=%s",
                task_id,
                context_id,
                cancel_abort_timeout_seconds,
                len(pending),
            )
        return

    if waitables:
        logger.info(
            "Cancel abort wait skipped task_id=%s context_id=%s abort_timeout_seconds=%.3f",
            task_id,
            context_id,
            cancel_abort_timeout_seconds,
        )
