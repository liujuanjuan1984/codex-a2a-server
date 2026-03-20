from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from a2a.server.events.event_queue import EventQueue
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart

from .codex_client import CodexClient
from .metrics import (
    CODEX_STREAM_RETRIES_TOTAL,
    INTERRUPT_REQUESTS_TOTAL,
    INTERRUPT_RESOLVED_TOTAL,
    TOOL_CALL_CHUNKS_EMITTED_TOTAL,
    get_metrics_registry,
)
from .output_mapping import (
    build_output_metadata,
    enqueue_artifact_update,
    extract_token_usage,
)
from .runtime_output_contracts import (
    build_interrupt_metadata,
    build_status_stream_metadata,
)
from .stream_chunks import (
    delta_chunks,
    extract_event_session_id,
    extract_stream_message_id,
    extract_stream_part_id,
    extract_stream_role,
    extract_stream_session_id,
    snapshot_chunks,
    tool_delta_chunks,
    tool_part_chunks,
    upsert_stream_part_state,
)
from .stream_interrupts import (
    diagnose_interrupt_event,
    extract_interrupt_asked_event,
    extract_interrupt_resolved_event,
)
from .stream_state import (
    BlockType,
    BufferedTextChunk,
    NormalizedStreamChunk,
    PendingDelta,
    StreamOutputState,
    StreamPartState,
    build_stream_artifact_metadata,
    flush_time_limit,
)

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


@dataclass
class StreamDiagnostics:
    started_at: float
    last_upstream_event_at: float | None = None
    last_visible_chunk_at: float | None = None
    completion_observed: bool = False
    emitted_chunk_count: int = 0
    suppressed_chunk_count: int = 0
    idle_log_count: int = 0
    last_idle_log_at: float | None = None

    def snapshot(self, *, now: float, stream_open: bool) -> dict[str, Any]:
        return {
            "stream_open": stream_open,
            "completion_observed": self.completion_observed,
            "emitted_chunk_count": self.emitted_chunk_count,
            "suppressed_chunk_count": self.suppressed_chunk_count,
            "started_ms_ago": int(max(0.0, now - self.started_at) * 1000),
            "last_upstream_event_ms_ago": (
                None
                if self.last_upstream_event_at is None
                else int(max(0.0, now - self.last_upstream_event_at) * 1000)
            ),
            "last_visible_chunk_ms_ago": (
                None
                if self.last_visible_chunk_at is None
                else int(max(0.0, now - self.last_visible_chunk_at) * 1000)
            ),
        }

    def should_log_idle(self, *, now: float, threshold_seconds: float) -> bool:
        threshold_base = self.last_idle_log_at or self.started_at
        if now - threshold_base < threshold_seconds:
            return False
        return (
            self.last_visible_chunk_at is None
            or (now - self.last_visible_chunk_at) >= threshold_seconds
        )


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
    part_states: dict[str, StreamPartState] = {}
    pending_deltas: defaultdict[str, list[PendingDelta]] = defaultdict(list)
    buffered_text_chunk: BufferedTextChunk | None = None
    backoff = 0.5
    max_backoff = 5.0
    resolved_idle_diagnostic_seconds = (
        _STREAM_IDLE_DIAGNOSTIC_SECONDS
        if idle_diagnostic_seconds is None
        else float(idle_diagnostic_seconds)
    )
    diagnostics = StreamDiagnostics(started_at=time.monotonic())
    logger.info(
        "Codex event stream started task_id=%s session_id=%s idle_diagnostic_seconds=%.1f",
        task_id,
        session_id,
        resolved_idle_diagnostic_seconds,
    )

    def maybe_log_idle(*, now: float) -> None:
        if not diagnostics.should_log_idle(
            now=now,
            threshold_seconds=resolved_idle_diagnostic_seconds,
        ):
            return
        diagnostics.last_idle_log_at = now
        diagnostics.idle_log_count += 1
        snapshot = diagnostics.snapshot(now=now, stream_open=not completion_event.is_set())
        logger.debug(
            "Codex event stream idle task_id=%s session_id=%s completion_observed=%s "
            "emitted_chunk_count=%s suppressed_chunk_count=%s started_ms_ago=%s "
            "last_upstream_event_ms_ago=%s last_visible_chunk_ms_ago=%s idle_log_count=%s",
            task_id,
            session_id,
            snapshot["completion_observed"],
            snapshot["emitted_chunk_count"],
            snapshot["suppressed_chunk_count"],
            snapshot["started_ms_ago"],
            snapshot["last_upstream_event_ms_ago"],
            snapshot["last_visible_chunk_ms_ago"],
            diagnostics.idle_log_count,
        )

    async def emit_chunk_now(chunk: NormalizedStreamChunk) -> None:
        resolved_message_id = stream_state.resolve_message_id(chunk.message_id)
        if isinstance(chunk.part, TextPart) and stream_state.should_drop_initial_user_echo(
            chunk.part.text,
            block_type=chunk.block_type,
            role=chunk.role,
        ):
            diagnostics.suppressed_chunk_count += 1
            return
        should_emit, effective_append = stream_state.register_chunk(
            block_type=chunk.block_type,
            content_key=chunk.content_key,
            append=chunk.append,
        )
        if not should_emit:
            diagnostics.suppressed_chunk_count += 1
            return
        sequence = stream_state.next_sequence()
        await enqueue_artifact_update(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id,
            artifact_id=artifact_id,
            part=chunk.part,
            append=effective_append,
            last_chunk=False,
            artifact_metadata=build_stream_artifact_metadata(
                block_type=chunk.block_type,
                source=chunk.source,
                message_id=resolved_message_id,
                role=chunk.role,
                sequence=sequence,
                event_id=stream_state.build_event_id(sequence),
            ),
        )
        diagnostics.emitted_chunk_count += 1
        diagnostics.last_visible_chunk_at = time.monotonic()
        if chunk.block_type == BlockType.TOOL_CALL:
            metrics.inc_counter(TOOL_CALL_CHUNKS_EMITTED_TOTAL)
        logger.debug(
            "Stream chunk task_id=%s session_id=%s block_type=%s append=%s text=%s",
            task_id,
            session_id,
            chunk.block_type,
            effective_append,
            chunk.part.text if isinstance(chunk.part, TextPart) else chunk.part.data,
        )

    def seconds_until_buffer_flush() -> float | None:
        if buffered_text_chunk is None:
            return None
        return max(
            0.0,
            flush_time_limit(buffered_text_chunk.block_type)
            - (time.monotonic() - buffered_text_chunk.started_at),
        )

    def seconds_until_idle_diagnostic() -> float | None:
        if completion_event.is_set():
            return None
        threshold_base = diagnostics.last_idle_log_at or diagnostics.started_at
        return max(
            0.0,
            resolved_idle_diagnostic_seconds - (time.monotonic() - threshold_base),
        )

    async def flush_buffered_text_chunk() -> None:
        nonlocal buffered_text_chunk
        if buffered_text_chunk is None:
            return
        chunk = buffered_text_chunk.to_chunk()
        buffered_text_chunk = None
        await emit_chunk_now(chunk)

    async def emit_chunks(chunks: list[NormalizedStreamChunk]) -> None:
        nonlocal buffered_text_chunk
        for chunk in chunks:
            if isinstance(chunk.part, TextPart) and chunk.block_type in {
                BlockType.TEXT,
                BlockType.REASONING,
            }:
                now = time.monotonic()
                if buffered_text_chunk is None:
                    buffered_text_chunk = BufferedTextChunk.from_chunk(chunk, now=now)
                elif buffered_text_chunk.can_merge(chunk):
                    buffered_text_chunk.append_chunk(chunk)
                else:
                    await flush_buffered_text_chunk()
                    buffered_text_chunk = BufferedTextChunk.from_chunk(chunk, now=now)
                if buffered_text_chunk is not None and buffered_text_chunk.should_flush(now=now):
                    await flush_buffered_text_chunk()
                continue

            await flush_buffered_text_chunk()
            await emit_chunk_now(chunk)

    async def emit_interrupt_status(
        *,
        state: TaskState,
        request_id: str,
        interrupt_type: str,
        details: Mapping[str, Any],
        phase: str,
        resolution: str | None = None,
    ) -> None:
        await flush_buffered_text_chunk()
        sequence = stream_state.next_sequence()
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=state),
                final=False,
                metadata=build_output_metadata(
                    session_id=session_id,
                    stream=build_status_stream_metadata(
                        message_id=stream_state.resolve_message_id(None),
                        event_id=stream_state.build_event_id(sequence),
                        source="interrupt",
                        sequence=sequence,
                    ),
                    interrupt=build_interrupt_metadata(
                        request_id=request_id,
                        interrupt_type=interrupt_type,
                        phase=phase,
                        details=details,
                        resolution=resolution,
                    ),
                ),
            )
        )
        if phase == "asked":
            metrics.inc_counter(INTERRUPT_REQUESTS_TOTAL)
        elif phase == "resolved":
            metrics.inc_counter(INTERRUPT_RESOLVED_TOTAL)

    try:
        while not stop_event.is_set():
            try:
                stream_iter = client.stream_events(
                    stop_event=stop_event, directory=directory
                ).__aiter__()
                pending_event_task: asyncio.Task[dict[str, Any]] | None = None
                while not stop_event.is_set():
                    if pending_event_task is None:
                        pending_event_task = asyncio.create_task(_next_stream_event(stream_iter))
                    wait_timeout = seconds_until_buffer_flush()
                    idle_timeout = seconds_until_idle_diagnostic()
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
                            if not diagnostics.completion_observed:
                                diagnostics.completion_observed = True
                                logger.info(
                                    "Codex event stream completion observed "
                                    "task_id=%s session_id=%s emitted_chunk_count=%s "
                                    "suppressed_chunk_count=%s",
                                    task_id,
                                    session_id,
                                    diagnostics.emitted_chunk_count,
                                    diagnostics.suppressed_chunk_count,
                                )
                            await flush_buffered_text_chunk()
                            pending_event_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await pending_event_task
                            pending_event_task = None
                            break
                        await flush_buffered_text_chunk()
                        maybe_log_idle(now=time.monotonic())
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
                    diagnostics.last_upstream_event_at = time.monotonic()
                    event_type = event.get("type")
                    if not isinstance(event_type, str):
                        maybe_log_idle(now=time.monotonic())
                        continue
                    props = event.get("properties")
                    if not isinstance(props, Mapping):
                        maybe_log_idle(now=time.monotonic())
                        continue
                    event_session_id = extract_event_session_id(event)
                    if event_session_id == session_id:
                        usage = extract_token_usage(event)
                        if usage is not None:
                            stream_state.ingest_token_usage(usage)
                        asked = extract_interrupt_asked_event(event)
                        if asked is not None:
                            request_id = asked["request_id"]
                            if stream_state.mark_interrupt_pending(request_id):
                                await emit_interrupt_status(
                                    state=TaskState.input_required,
                                    request_id=request_id,
                                    interrupt_type=asked["interrupt_type"],
                                    details=asked["details"],
                                    phase="asked",
                                )
                        resolved = extract_interrupt_resolved_event(event)
                        if resolved is not None:
                            if stream_state.clear_interrupt_pending(resolved["request_id"]):
                                await emit_interrupt_status(
                                    state=TaskState.working,
                                    request_id=resolved["request_id"],
                                    interrupt_type=resolved["interrupt_type"],
                                    details={},
                                    phase="resolved",
                                    resolution=resolved["resolution"],
                                )
                        interrupt_diagnostic = diagnose_interrupt_event(event)
                        if interrupt_diagnostic is not None and asked is None and resolved is None:
                            logger.debug(
                                "Interrupt payload not adapted reason=%s payload=%s",
                                interrupt_diagnostic,
                                event,
                            )
                    maybe_log_idle(now=time.monotonic())
                    if event_type not in {"message.part.updated", "message.part.delta"}:
                        continue
                    part = props.get("part")
                    if not isinstance(part, Mapping):
                        part = {}
                    if extract_stream_session_id(part, props) != session_id:
                        continue
                    message_id = extract_stream_message_id(part, props)
                    part_id = extract_stream_part_id(part, props)
                    if not part_id:
                        continue

                    if event_type == "message.part.delta":
                        field = props.get("field")
                        delta = props.get("delta")
                        if field != "text" or not isinstance(delta, str) or not delta:
                            continue
                        state = part_states.get(part_id)
                        if state is None:
                            pending_deltas[part_id].append(
                                PendingDelta(
                                    field=field,
                                    delta=delta,
                                    message_id=message_id,
                                )
                            )
                            continue
                        if state.role in {"user", "system"}:
                            continue
                        if state.block_type == BlockType.TOOL_CALL:
                            delta_event_chunks = tool_delta_chunks(
                                state=state,
                                delta_value=delta,
                                message_id=message_id,
                                source="delta_event",
                                task_id=task_id,
                                session_id=session_id,
                            )
                        else:
                            delta_event_chunks = delta_chunks(
                                state=state,
                                delta_text=delta,
                                message_id=message_id,
                                source="delta_event",
                            )
                        if delta_event_chunks:
                            await emit_chunks(delta_event_chunks)
                        continue

                    role = extract_stream_role(part, props)
                    state = upsert_stream_part_state(
                        part_states=part_states,
                        part_id=part_id,
                        part=part,
                        props=props,
                        role=role,
                        message_id=message_id,
                    )
                    if state is None:
                        pending_deltas.pop(part_id, None)
                        continue
                    if state.role in {"user", "system"}:
                        pending_deltas.pop(part_id, None)
                        continue

                    chunks: list[NormalizedStreamChunk] = []
                    pending = pending_deltas.pop(part_id, [])
                    for buffered in pending:
                        if buffered.field != "text":
                            continue
                        if state.block_type == BlockType.TOOL_CALL:
                            chunks.extend(
                                tool_delta_chunks(
                                    state=state,
                                    delta_value=buffered.delta,
                                    message_id=buffered.message_id,
                                    source="delta_event_buffered",
                                    task_id=task_id,
                                    session_id=session_id,
                                )
                            )
                        else:
                            chunks.extend(
                                delta_chunks(
                                    state=state,
                                    delta_text=buffered.delta,
                                    message_id=buffered.message_id,
                                    source="delta_event_buffered",
                                )
                            )

                    delta = props.get("delta")
                    if state.block_type == BlockType.TOOL_CALL:
                        if delta is not None and (not isinstance(delta, str) or delta):
                            chunks.extend(
                                tool_delta_chunks(
                                    state=state,
                                    delta_value=delta,
                                    message_id=message_id,
                                    source="delta",
                                    task_id=task_id,
                                    session_id=session_id,
                                )
                            )
                        else:
                            chunks.extend(
                                tool_part_chunks(
                                    state=state,
                                    part=part,
                                    message_id=message_id,
                                )
                            )
                    elif isinstance(delta, str) and delta:
                        chunks.extend(
                            delta_chunks(
                                state=state,
                                delta_text=delta,
                                message_id=message_id,
                                source="delta",
                            )
                        )
                    elif isinstance(part.get("text"), str):
                        chunks.extend(
                            snapshot_chunks(
                                state=state,
                                snapshot=part["text"],
                                message_id=message_id,
                                task_id=task_id,
                                session_id=session_id,
                            )
                        )

                    if chunks:
                        await emit_chunks(chunks)
                    maybe_log_idle(now=time.monotonic())

                break
            except Exception:
                if stop_event.is_set():
                    break
                metrics.inc_counter(CODEX_STREAM_RETRIES_TOTAL)
                logger.exception(
                    "Codex event stream failed; retrying "
                    "task_id=%s session_id=%s backoff_seconds=%.1f",
                    task_id,
                    session_id,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            finally:
                await flush_buffered_text_chunk()
    except Exception:
        logger.exception("Codex event stream failed task_id=%s session_id=%s", task_id, session_id)
    finally:
        if completion_event.is_set() and not diagnostics.completion_observed:
            diagnostics.completion_observed = True
            logger.info(
                "Codex event stream completion observed "
                "task_id=%s session_id=%s emitted_chunk_count=%s suppressed_chunk_count=%s",
                task_id,
                session_id,
                diagnostics.emitted_chunk_count,
                diagnostics.suppressed_chunk_count,
            )
        snapshot = diagnostics.snapshot(now=time.monotonic(), stream_open=False)
        logger.info(
            "Codex event stream closed task_id=%s session_id=%s completion_observed=%s "
            "emitted_chunk_count=%s suppressed_chunk_count=%s started_ms_ago=%s "
            "last_upstream_event_ms_ago=%s last_visible_chunk_ms_ago=%s idle_log_count=%s",
            task_id,
            session_id,
            snapshot["completion_observed"],
            snapshot["emitted_chunk_count"],
            snapshot["suppressed_chunk_count"],
            snapshot["started_ms_ago"],
            snapshot["last_upstream_event_ms_ago"],
            snapshot["last_visible_chunk_ms_ago"],
            diagnostics.idle_log_count,
        )
