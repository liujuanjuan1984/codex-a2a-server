from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from a2a.server.events.event_queue import EventQueue
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart

from codex_a2a_server.contracts.runtime_output import (
    build_interrupt_metadata,
    build_status_stream_metadata,
)
from codex_a2a_server.execution.output_mapping import (
    build_output_metadata,
    enqueue_artifact_update,
    extract_token_usage,
)
from codex_a2a_server.execution.stream_chunks import (
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
from codex_a2a_server.execution.stream_interrupts import (
    diagnose_interrupt_event,
    extract_interrupt_asked_event,
    extract_interrupt_resolved_event,
)
from codex_a2a_server.execution.stream_state import (
    BlockType,
    BufferedTextChunk,
    NormalizedStreamChunk,
    PendingDelta,
    StreamOutputState,
    StreamPartState,
    build_stream_artifact_metadata,
    flush_time_limit,
)
from codex_a2a_server.metrics import (
    INTERRUPT_REQUESTS_TOTAL,
    INTERRUPT_RESOLVED_TOTAL,
    TOOL_CALL_CHUNKS_EMITTED_TOTAL,
    get_metrics_registry,
)

metrics = get_metrics_registry()


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


class StreamEventProcessor:
    def __init__(
        self,
        *,
        task_id: str,
        context_id: str,
        session_id: str,
        artifact_id: str,
        stream_state: StreamOutputState,
        event_queue: EventQueue,
        completion_event,
        idle_diagnostic_seconds: float,
    ) -> None:
        self._task_id = task_id
        self._context_id = context_id
        self._session_id = session_id
        self._artifact_id = artifact_id
        self._stream_state = stream_state
        self._event_queue = event_queue
        self._completion_event = completion_event
        self._idle_diagnostic_seconds = idle_diagnostic_seconds
        self._part_states: dict[str, StreamPartState] = {}
        self._pending_deltas: defaultdict[str, list[PendingDelta]] = defaultdict(list)
        self._buffered_text_chunk: BufferedTextChunk | None = None
        self._diagnostics = StreamDiagnostics(started_at=time.monotonic())

    def log_started(self, logger) -> None:  # noqa: ANN001
        logger.info(
            "Codex event stream started task_id=%s session_id=%s idle_diagnostic_seconds=%.1f",
            self._task_id,
            self._session_id,
            self._idle_diagnostic_seconds,
        )

    def seconds_until_buffer_flush(self) -> float | None:
        if self._buffered_text_chunk is None:
            return None
        return max(
            0.0,
            flush_time_limit(self._buffered_text_chunk.block_type)
            - (time.monotonic() - self._buffered_text_chunk.started_at),
        )

    def seconds_until_idle_diagnostic(self) -> float | None:
        if self._completion_event.is_set():
            return None
        threshold_base = self._diagnostics.last_idle_log_at or self._diagnostics.started_at
        return max(
            0.0,
            self._idle_diagnostic_seconds - (time.monotonic() - threshold_base),
        )

    def maybe_log_idle(self, logger) -> None:  # noqa: ANN001
        now = time.monotonic()
        if not self._diagnostics.should_log_idle(
            now=now,
            threshold_seconds=self._idle_diagnostic_seconds,
        ):
            return
        self._diagnostics.last_idle_log_at = now
        self._diagnostics.idle_log_count += 1
        snapshot = self._diagnostics.snapshot(now=now, stream_open=not self._completion_event.is_set())
        logger.debug(
            "Codex event stream idle task_id=%s session_id=%s completion_observed=%s "
            "emitted_chunk_count=%s suppressed_chunk_count=%s started_ms_ago=%s "
            "last_upstream_event_ms_ago=%s last_visible_chunk_ms_ago=%s idle_log_count=%s",
            self._task_id,
            self._session_id,
            snapshot["completion_observed"],
            snapshot["emitted_chunk_count"],
            snapshot["suppressed_chunk_count"],
            snapshot["started_ms_ago"],
            snapshot["last_upstream_event_ms_ago"],
            snapshot["last_visible_chunk_ms_ago"],
            self._diagnostics.idle_log_count,
        )

    async def observe_completion(self, logger) -> None:  # noqa: ANN001
        if self._diagnostics.completion_observed:
            return
        self._diagnostics.completion_observed = True
        logger.info(
            "Codex event stream completion observed task_id=%s session_id=%s "
            "emitted_chunk_count=%s suppressed_chunk_count=%s",
            self._task_id,
            self._session_id,
            self._diagnostics.emitted_chunk_count,
            self._diagnostics.suppressed_chunk_count,
        )

    async def close(self, logger) -> None:  # noqa: ANN001
        await self.flush_buffered_text_chunk()
        if self._completion_event.is_set():
            await self.observe_completion(logger)
        snapshot = self._diagnostics.snapshot(now=time.monotonic(), stream_open=False)
        logger.info(
            "Codex event stream closed task_id=%s session_id=%s completion_observed=%s "
            "emitted_chunk_count=%s suppressed_chunk_count=%s started_ms_ago=%s "
            "last_upstream_event_ms_ago=%s last_visible_chunk_ms_ago=%s idle_log_count=%s",
            self._task_id,
            self._session_id,
            snapshot["completion_observed"],
            snapshot["emitted_chunk_count"],
            snapshot["suppressed_chunk_count"],
            snapshot["started_ms_ago"],
            snapshot["last_upstream_event_ms_ago"],
            snapshot["last_visible_chunk_ms_ago"],
            self._diagnostics.idle_log_count,
        )

    async def flush_buffered_text_chunk(self) -> None:
        if self._buffered_text_chunk is None:
            return
        chunk = self._buffered_text_chunk.to_chunk()
        self._buffered_text_chunk = None
        await self._emit_chunk_now(chunk)

    async def _emit_chunk_now(self, chunk: NormalizedStreamChunk) -> None:
        resolved_message_id = self._stream_state.resolve_message_id(chunk.message_id)
        if isinstance(chunk.part, TextPart) and self._stream_state.should_drop_initial_user_echo(
            chunk.part.text,
            block_type=chunk.block_type,
            role=chunk.role,
        ):
            self._diagnostics.suppressed_chunk_count += 1
            return
        should_emit, effective_append = self._stream_state.register_chunk(
            block_type=chunk.block_type,
            content_key=chunk.content_key,
            append=chunk.append,
        )
        if not should_emit:
            self._diagnostics.suppressed_chunk_count += 1
            return
        sequence = self._stream_state.next_sequence()
        await enqueue_artifact_update(
            event_queue=self._event_queue,
            task_id=self._task_id,
            context_id=self._context_id,
            artifact_id=self._artifact_id,
            part=chunk.part,
            append=effective_append,
            last_chunk=False,
            artifact_metadata=build_stream_artifact_metadata(
                block_type=chunk.block_type,
                source=chunk.source,
                message_id=resolved_message_id,
                role=chunk.role,
                sequence=sequence,
                event_id=self._stream_state.build_event_id(sequence),
            ),
        )
        self._diagnostics.emitted_chunk_count += 1
        self._diagnostics.last_visible_chunk_at = time.monotonic()
        if chunk.block_type == BlockType.TOOL_CALL:
            metrics.inc_counter(TOOL_CALL_CHUNKS_EMITTED_TOTAL)

    async def emit_chunks(self, chunks: list[NormalizedStreamChunk]) -> None:
        for chunk in chunks:
            if isinstance(chunk.part, TextPart) and chunk.block_type in {
                BlockType.TEXT,
                BlockType.REASONING,
            }:
                now = time.monotonic()
                if self._buffered_text_chunk is None:
                    self._buffered_text_chunk = BufferedTextChunk.from_chunk(chunk, now=now)
                elif self._buffered_text_chunk.can_merge(chunk):
                    self._buffered_text_chunk.append_chunk(chunk)
                else:
                    await self.flush_buffered_text_chunk()
                    self._buffered_text_chunk = BufferedTextChunk.from_chunk(chunk, now=now)
                if (
                    self._buffered_text_chunk is not None
                    and self._buffered_text_chunk.should_flush(now=now)
                ):
                    await self.flush_buffered_text_chunk()
                continue

            await self.flush_buffered_text_chunk()
            await self._emit_chunk_now(chunk)

    async def _emit_interrupt_status(
        self,
        *,
        state: TaskState,
        request_id: str,
        interrupt_type: str,
        details: Mapping[str, Any],
        phase: str,
        resolution: str | None = None,
    ) -> None:
        await self.flush_buffered_text_chunk()
        sequence = self._stream_state.next_sequence()
        await self._event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                status=TaskStatus(state=state),
                final=False,
                metadata=build_output_metadata(
                    session_id=self._session_id,
                    stream=build_status_stream_metadata(
                        message_id=self._stream_state.resolve_message_id(None),
                        event_id=self._stream_state.build_event_id(sequence),
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

    async def handle_event(self, event: dict[str, Any], logger) -> None:  # noqa: ANN001
        self._diagnostics.last_upstream_event_at = time.monotonic()
        event_type = event.get("type")
        if not isinstance(event_type, str):
            self.maybe_log_idle(logger)
            return
        props = event.get("properties")
        if not isinstance(props, Mapping):
            self.maybe_log_idle(logger)
            return

        event_session_id = extract_event_session_id(event)
        if event_session_id == self._session_id:
            usage = extract_token_usage(event)
            if usage is not None:
                self._stream_state.ingest_token_usage(usage)
            asked = extract_interrupt_asked_event(event)
            if asked is not None:
                request_id = asked["request_id"]
                if self._stream_state.mark_interrupt_pending(request_id):
                    await self._emit_interrupt_status(
                        state=TaskState.input_required,
                        request_id=request_id,
                        interrupt_type=asked["interrupt_type"],
                        details=asked["details"],
                        phase="asked",
                    )
            resolved = extract_interrupt_resolved_event(event)
            if resolved is not None:
                if self._stream_state.clear_interrupt_pending(resolved["request_id"]):
                    await self._emit_interrupt_status(
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
        self.maybe_log_idle(logger)
        if event_type not in {"message.part.updated", "message.part.delta"}:
            return
        part = props.get("part")
        if not isinstance(part, Mapping):
            part = {}
        if extract_stream_session_id(part, props) != self._session_id:
            return
        message_id = extract_stream_message_id(part, props)
        part_id = extract_stream_part_id(part, props)
        if not part_id:
            return

        if event_type == "message.part.delta":
            field = props.get("field")
            delta = props.get("delta")
            if field != "text" or not isinstance(delta, str) or not delta:
                return
            state = self._part_states.get(part_id)
            if state is None:
                self._pending_deltas[part_id].append(
                    PendingDelta(
                        field=field,
                        delta=delta,
                        message_id=message_id,
                    )
                )
                return
            if state.role in {"user", "system"}:
                return
            if state.block_type == BlockType.TOOL_CALL:
                delta_event_chunks = tool_delta_chunks(
                    state=state,
                    delta_value=delta,
                    message_id=message_id,
                    source="delta_event",
                    task_id=self._task_id,
                    session_id=self._session_id,
                )
            else:
                delta_event_chunks = delta_chunks(
                    state=state,
                    delta_text=delta,
                    message_id=message_id,
                    source="delta_event",
                )
            if delta_event_chunks:
                await self.emit_chunks(delta_event_chunks)
            return

        role = extract_stream_role(part, props)
        state = upsert_stream_part_state(
            part_states=self._part_states,
            part_id=part_id,
            part=part,
            props=props,
            role=role,
            message_id=message_id,
        )
        if state is None:
            self._pending_deltas.pop(part_id, None)
            return
        if state.role in {"user", "system"}:
            self._pending_deltas.pop(part_id, None)
            return

        chunks: list[NormalizedStreamChunk] = []
        pending = self._pending_deltas.pop(part_id, [])
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
                        task_id=self._task_id,
                        session_id=self._session_id,
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
                        task_id=self._task_id,
                        session_id=self._session_id,
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
                    task_id=self._task_id,
                    session_id=self._session_id,
                )
            )

        if chunks:
            await self.emit_chunks(chunks)
        self.maybe_log_idle(logger)
