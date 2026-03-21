from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from a2a.server.events.event_queue import EventQueue

from codex_a2a_server.execution.stream_chunks import (
    extract_event_session_id,
    extract_stream_message_id,
    extract_stream_part_id,
    extract_stream_session_id,
)
from codex_a2a_server.execution.stream_interrupts import extract_interrupt_resolved_event
from codex_a2a_server.execution.stream_processor import StreamEventProcessor
from codex_a2a_server.execution.stream_state import (
    BlockType,
    StreamOutputState,
    build_stream_artifact_metadata,
)
from codex_a2a_server.metrics import CODEX_STREAM_RETRIES_TOTAL, get_metrics_registry
from codex_a2a_server.upstream.client import CodexClient

logger = logging.getLogger(__name__)
metrics = get_metrics_registry()

_STREAM_COMPLETION_DRAIN_SECONDS = 0.05
_STREAM_IDLE_DIAGNOSTIC_SECONDS = 60.0
__all__ = [
    "BlockType",
    "StreamOutputState",
    "build_stream_artifact_metadata",
    "consume_codex_stream",
    "extract_event_session_id",
    "extract_interrupt_resolved_event",
    "extract_stream_message_id",
    "extract_stream_part_id",
    "extract_stream_session_id",
]


async def _next_stream_event(stream_iter: AsyncIterator[dict[str, Any]]) -> dict[str, Any]:
    return await anext(stream_iter)


async def consume_codex_stream(
    *,
    client: CodexClient,
    session_id: str,
    task_id: str,
    context_id: str,
    artifact_id: str,
    stream_state: StreamOutputState,
    event_queue: EventQueue,
    stop_event: asyncio.Event,
    completion_event: asyncio.Event,
    idle_diagnostic_seconds: float | None = None,
    directory: str | None = None,
) -> None:
    backoff = 0.5
    max_backoff = 5.0
    resolved_idle_diagnostic_seconds = (
        _STREAM_IDLE_DIAGNOSTIC_SECONDS
        if idle_diagnostic_seconds is None
        else float(idle_diagnostic_seconds)
    )
    processor = StreamEventProcessor(
        task_id=task_id,
        context_id=context_id,
        session_id=session_id,
        artifact_id=artifact_id,
        stream_state=stream_state,
        event_queue=event_queue,
        completion_event=completion_event,
        idle_diagnostic_seconds=resolved_idle_diagnostic_seconds,
    )
    processor.log_started(logger)

    try:
        while not stop_event.is_set():
            try:
                stream_iter = client.stream_events(
                    stop_event=stop_event,
                    directory=directory,
                ).__aiter__()
                pending_event_task: asyncio.Task[dict[str, Any]] | None = None
                while not stop_event.is_set():
                    if pending_event_task is None:
                        pending_event_task = asyncio.create_task(_next_stream_event(stream_iter))
                    wait_timeout = processor.seconds_until_buffer_flush()
                    idle_timeout = processor.seconds_until_idle_diagnostic()
                    if idle_timeout is not None:
                        wait_timeout = (
                            idle_timeout
                            if wait_timeout is None
                            else min(wait_timeout, idle_timeout)
                        )
                    if completion_event.is_set():
                        if wait_timeout is None:
                            wait_timeout = _STREAM_COMPLETION_DRAIN_SECONDS
                        else:
                            wait_timeout = min(wait_timeout, _STREAM_COMPLETION_DRAIN_SECONDS)
                    done, _ = await asyncio.wait(
                        {pending_event_task},
                        timeout=wait_timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if pending_event_task not in done:
                        if completion_event.is_set():
                            await processor.observe_completion(logger)
                            await processor.flush_buffered_text_chunk()
                            pending_event_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await pending_event_task
                            pending_event_task = None
                            break
                        await processor.flush_buffered_text_chunk()
                        processor.maybe_log_idle(logger)
                        continue
                    try:
                        event = pending_event_task.result()
                    except StopAsyncIteration:
                        pending_event_task = None
                        break
                    finally:
                        pending_event_task = None
                    if stop_event.is_set():
                        break
                    await processor.handle_event(event, logger)

                break
            except Exception:
                if stop_event.is_set():
                    break
                metrics.inc_counter(CODEX_STREAM_RETRIES_TOTAL)
                logger.exception(
                    "Codex event stream failed; retrying task_id=%s session_id=%s "
                    "backoff_seconds=%.1f",
                    task_id,
                    session_id,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            finally:
                await processor.flush_buffered_text_chunk()
    except Exception:
        logger.exception("Codex event stream failed task_id=%s session_id=%s", task_id, session_id)
    finally:
        await processor.close(logger)
