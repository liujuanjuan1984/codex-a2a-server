from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from a2a.server.events.event_queue import EventQueue
from a2a.types import DataPart, TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart

from .codex_client import CodexClient
from .output_mapping import (
    build_output_metadata,
    enqueue_artifact_update,
    extract_token_usage,
    merge_token_usage,
)
from .tool_call_payloads import (
    ToolCallPayload,
    as_tool_call_payload,
    normalize_tool_call_payload,
    serialize_tool_call_payload,
    tool_call_state_payload_from_part,
)

_INTERRUPT_ASKED_EVENT_TYPES = {"permission.asked", "question.asked"}
_INTERRUPT_RESOLVED_EVENT_TYPES = {"permission.replied", "question.replied", "question.rejected"}
_STREAM_COMPLETION_DRAIN_SECONDS = 0.05
_STREAM_TEXT_FLUSH_CHARS = 120
_STREAM_TEXT_FLUSH_SECONDS = 0.2
_STREAM_REASONING_FLUSH_CHARS = 240
_STREAM_REASONING_FLUSH_SECONDS = 0.35


class BlockType(str, Enum):
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"


def flush_char_limit(block_type: BlockType) -> int:
    if block_type == BlockType.REASONING:
        return _STREAM_REASONING_FLUSH_CHARS
    return _STREAM_TEXT_FLUSH_CHARS


def flush_time_limit(block_type: BlockType) -> float:
    if block_type == BlockType.REASONING:
        return _STREAM_REASONING_FLUSH_SECONDS
    return _STREAM_TEXT_FLUSH_SECONDS


@dataclass(frozen=True)
class NormalizedStreamChunk:
    part: TextPart | DataPart
    content_key: str
    append: bool
    block_type: BlockType
    source: str
    message_id: str | None
    role: str | None
    part_id: str | None


@dataclass(frozen=True)
class PendingDelta:
    field: str
    delta: str
    message_id: str | None


@dataclass
class StreamPartState:
    part_id: str
    block_type: BlockType
    message_id: str | None
    role: str | None
    buffer: str = ""
    saw_delta: bool = False
    emitted_tool_chunks: int = 0
    last_tool_state_payload: str | None = None


@dataclass
class BufferedTextChunk:
    block_type: BlockType
    part_id: str | None
    message_id: str | None
    role: str | None
    source: str
    append: bool
    text: str
    started_at: float

    @classmethod
    def from_chunk(cls, chunk: NormalizedStreamChunk, *, now: float) -> BufferedTextChunk:
        text = chunk.part.text if isinstance(chunk.part, TextPart) else ""
        return cls(
            block_type=chunk.block_type,
            part_id=chunk.part_id,
            message_id=chunk.message_id,
            role=chunk.role,
            source=chunk.source,
            append=chunk.append,
            text=text,
            started_at=now,
        )

    def can_merge(self, chunk: NormalizedStreamChunk) -> bool:
        if not isinstance(chunk.part, TextPart):
            return False
        if chunk.block_type not in {BlockType.TEXT, BlockType.REASONING}:
            return False
        return (
            self.block_type == chunk.block_type
            and self.part_id == chunk.part_id
            and self.message_id == chunk.message_id
            and self.role == chunk.role
            and self.source == chunk.source
            and self.append == chunk.append
        )

    def append_chunk(self, chunk: NormalizedStreamChunk) -> None:
        if not isinstance(chunk.part, TextPart):
            return
        self.text = f"{self.text}{chunk.part.text}"

    def should_flush(self, *, now: float) -> bool:
        return len(self.text) >= flush_char_limit(self.block_type) or (
            now - self.started_at
        ) >= flush_time_limit(self.block_type)

    def to_chunk(self) -> NormalizedStreamChunk:
        return NormalizedStreamChunk(
            part=TextPart(text=self.text),
            content_key=self.text,
            append=self.append,
            block_type=self.block_type,
            source=self.source,
            message_id=self.message_id,
            role=self.role,
            part_id=self.part_id,
        )


@dataclass
class StreamOutputState:
    user_text: str
    stable_message_id: str
    event_id_namespace: str
    content_buffers: dict[BlockType, str] = field(default_factory=dict)
    token_usage: dict[str, Any] | None = None
    pending_interrupt_request_ids: set[str] = field(default_factory=set)
    saw_any_chunk: bool = False
    emitted_stream_chunk: bool = False
    sequence: int = 0

    def matches_expected_message(self, message_id: str | None) -> bool:
        return True

    def should_drop_initial_user_echo(
        self,
        text: str,
        *,
        block_type: BlockType,
        role: str | None,
    ) -> bool:
        if role is not None:
            return False
        if block_type != BlockType.TEXT:
            return False
        if self.saw_any_chunk:
            return False
        user_text = self.user_text.strip()
        return bool(user_text) and text.strip() == user_text

    def register_chunk(
        self, *, block_type: BlockType, content_key: str, append: bool
    ) -> tuple[bool, bool]:
        previous = self.content_buffers.get(block_type, "")
        next_value = f"{previous}{content_key}" if append else content_key
        if next_value == previous:
            return False, False
        self.content_buffers[block_type] = next_value
        self.saw_any_chunk = True
        effective_append = self.emitted_stream_chunk
        self.emitted_stream_chunk = True
        return True, effective_append

    def should_emit_final_snapshot(self, text: str) -> bool:
        if not text.strip():
            return False
        existing = self.content_buffers.get(BlockType.TEXT, "")
        if existing.strip() == text.strip():
            return False
        self.content_buffers[BlockType.TEXT] = text
        self.saw_any_chunk = True
        return True

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def resolve_message_id(self, message_id: str | None) -> str:
        if isinstance(message_id, str):
            normalized = message_id.strip()
            if normalized:
                return normalized
        return self.stable_message_id

    def build_event_id(self, sequence: int) -> str:
        return f"{self.event_id_namespace}:{sequence}"

    def ingest_token_usage(self, usage: Mapping[str, Any] | None) -> None:
        self.token_usage = merge_token_usage(self.token_usage, usage)

    def mark_interrupt_pending(self, request_id: str) -> bool:
        normalized = request_id.strip()
        if not normalized:
            return False
        if normalized in self.pending_interrupt_request_ids:
            return False
        self.pending_interrupt_request_ids.add(normalized)
        return True

    def clear_interrupt_pending(self, request_id: str) -> bool:
        normalized = request_id.strip()
        if not normalized or normalized not in self.pending_interrupt_request_ids:
            return False
        self.pending_interrupt_request_ids.discard(normalized)
        return True


def build_stream_artifact_metadata(
    *,
    block_type: BlockType,
    source: str,
    message_id: str | None = None,
    role: str | None = None,
    sequence: int | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    stream_meta: dict[str, Any] = {
        "block_type": block_type.value,
        "source": source,
    }
    if message_id:
        stream_meta["message_id"] = message_id
    if role:
        stream_meta["role"] = role
    if sequence is not None:
        stream_meta["sequence"] = sequence
    if event_id:
        stream_meta["event_id"] = event_id
    return {"shared": {"stream": stream_meta}}


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
    logger: logging.Logger,
    directory: str | None = None,
) -> None:
    part_states: dict[str, StreamPartState] = {}
    pending_deltas: defaultdict[str, list[PendingDelta]] = defaultdict(list)
    buffered_text_chunk: BufferedTextChunk | None = None
    backoff = 0.5
    max_backoff = 5.0

    async def emit_chunk_now(chunk: NormalizedStreamChunk) -> None:
        if not stream_state.matches_expected_message(chunk.message_id):
            return
        resolved_message_id = stream_state.resolve_message_id(chunk.message_id)
        if isinstance(chunk.part, TextPart) and stream_state.should_drop_initial_user_echo(
            chunk.part.text,
            block_type=chunk.block_type,
            role=chunk.role,
        ):
            return
        should_emit, effective_append = stream_state.register_chunk(
            block_type=chunk.block_type,
            content_key=chunk.content_key,
            append=chunk.append,
        )
        if not should_emit:
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
        codex_private: Mapping[str, Any] | None = None,
    ) -> None:
        await flush_buffered_text_chunk()
        sequence = stream_state.next_sequence()
        interrupt_payload: dict[str, Any] = {
            "request_id": request_id,
            "type": interrupt_type,
            "phase": phase,
            "details": dict(details),
        }
        if resolution is not None:
            interrupt_payload["resolution"] = resolution
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=state),
                final=False,
                metadata=build_output_metadata(
                    session_id=session_id,
                    stream={
                        "message_id": stream_state.resolve_message_id(None),
                        "event_id": stream_state.build_event_id(sequence),
                        "source": "interrupt",
                        "sequence": sequence,
                    },
                    interrupt=interrupt_payload,
                    codex_private=({"interrupt": dict(codex_private)} if codex_private else None),
                ),
            )
        )

    def new_chunk(
        *,
        part: TextPart | DataPart,
        content_key: str,
        append: bool,
        block_type: BlockType,
        source: str,
        message_id: str | None,
        role: str | None,
        part_id: str | None,
    ) -> NormalizedStreamChunk:
        return NormalizedStreamChunk(
            part=part,
            content_key=content_key,
            append=append,
            block_type=block_type,
            source=source,
            message_id=message_id,
            role=role,
            part_id=part_id,
        )

    def upsert_part_state(
        *,
        part_id: str,
        part: Mapping[str, Any],
        props: Mapping[str, Any],
        role: str | None,
        message_id: str | None,
    ) -> StreamPartState | None:
        block_type = resolve_stream_block_type(part, props)
        if block_type is None:
            return None
        state = part_states.get(part_id)
        if state is None:
            state = StreamPartState(
                part_id=part_id,
                block_type=block_type,
                message_id=message_id,
                role=role,
            )
            part_states[part_id] = state
            return state
        state.part_id = part_id
        state.block_type = block_type
        if role is not None:
            state.role = role
        if message_id:
            state.message_id = message_id
        return state

    def delta_chunks(
        *,
        state: StreamPartState,
        delta_text: str,
        message_id: str | None,
        source: str,
    ) -> list[NormalizedStreamChunk]:
        if not delta_text:
            return []
        if message_id:
            state.message_id = message_id
        state.buffer = f"{state.buffer}{delta_text}"
        state.saw_delta = True
        return [
            new_chunk(
                part=TextPart(text=delta_text),
                content_key=delta_text,
                append=True,
                block_type=state.block_type,
                source=source,
                message_id=state.message_id,
                role=state.role,
                part_id=state.part_id,
            )
        ]

    def snapshot_chunks(
        *,
        state: StreamPartState,
        snapshot: str,
        message_id: str | None,
        part_id: str,
    ) -> list[NormalizedStreamChunk]:
        if message_id:
            state.message_id = message_id
        previous = state.buffer
        if snapshot == previous:
            return []
        if snapshot.startswith(previous):
            delta_text = snapshot[len(previous) :]
            state.buffer = snapshot
            if not delta_text:
                return []
            return [
                new_chunk(
                    part=TextPart(text=delta_text),
                    content_key=delta_text,
                    append=True,
                    block_type=state.block_type,
                    source="part_text_diff",
                    message_id=state.message_id,
                    role=state.role,
                    part_id=state.part_id,
                )
            ]
        state.buffer = snapshot
        logger.warning(
            "Suppressing non-prefix snapshot rewrite "
            "task_id=%s session_id=%s part_id=%s block_type=%s had_delta=%s",
            task_id,
            session_id,
            part_id,
            state.block_type.value,
            state.saw_delta,
        )
        return []

    def emit_tool_payload_chunk(
        *,
        state: StreamPartState,
        payload: ToolCallPayload,
        message_id: str | None,
        source: str,
    ) -> list[NormalizedStreamChunk]:
        tool_chunk = serialize_tool_call_payload(payload)
        if message_id:
            state.message_id = message_id
        if payload.kind == "state" and tool_chunk == state.last_tool_state_payload:
            return []
        append = state.emitted_tool_chunks > 0
        state.emitted_tool_chunks += 1
        if payload.kind == "state":
            state.last_tool_state_payload = tool_chunk
        content_key = tool_chunk if not append else f"\n{tool_chunk}"
        return [
            new_chunk(
                part=DataPart(data=as_tool_call_payload(payload)),
                content_key=content_key,
                append=append,
                block_type=state.block_type,
                source=source,
                message_id=state.message_id,
                role=state.role,
                part_id=state.part_id,
            )
        ]

    def tool_part_chunks(
        *,
        state: StreamPartState,
        part: Mapping[str, Any],
        message_id: str | None,
    ) -> list[NormalizedStreamChunk]:
        payload = tool_call_state_payload_from_part(part)
        if payload is None:
            return []
        return emit_tool_payload_chunk(
            state=state,
            payload=payload,
            message_id=message_id,
            source="tool_part_update",
        )

    def tool_delta_chunks(
        *,
        state: StreamPartState,
        delta_value: Any,
        message_id: str | None,
        source: str,
    ) -> list[NormalizedStreamChunk]:
        if not isinstance(delta_value, Mapping):
            logger.warning(
                "Suppressing non-structured tool_call payload "
                "task_id=%s session_id=%s source=%s payload=%s",
                task_id,
                session_id,
                source,
                delta_value,
            )
            return []
        payload = normalize_tool_call_payload(delta_value)
        if payload is None:
            logger.warning(
                "Suppressing unrecognized tool_call payload "
                "task_id=%s session_id=%s source=%s payload=%s",
                task_id,
                session_id,
                source,
                delta_value,
            )
            return []
        return emit_tool_payload_chunk(
            state=state,
            payload=payload,
            message_id=message_id,
            source=source,
        )

    try:
        while not stop_event.is_set():
            try:
                stream_iter = client.stream_events(
                    stop_event=stop_event, directory=directory
                ).__aiter__()
                pending_event_task: asyncio.Task[dict[str, Any]] | None = None
                while not stop_event.is_set():
                    if pending_event_task is None:
                        pending_event_task = asyncio.create_task(anext(stream_iter))
                    wait_timeout = seconds_until_buffer_flush()
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
                            await flush_buffered_text_chunk()
                            pending_event_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await pending_event_task
                            pending_event_task = None
                            break
                        await flush_buffered_text_chunk()
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
                    event_type = event.get("type")
                    if not isinstance(event_type, str):
                        continue
                    props = event.get("properties")
                    if not isinstance(props, Mapping):
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
                                    codex_private=asked.get("codex_private"),
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
                            chunks = tool_delta_chunks(
                                state=state,
                                delta_value=delta,
                                message_id=message_id,
                                source="delta_event",
                            )
                        else:
                            chunks = delta_chunks(
                                state=state,
                                delta_text=delta,
                                message_id=message_id,
                                source="delta_event",
                            )
                        if chunks:
                            await emit_chunks(chunks)
                        continue

                    role = extract_stream_role(part, props)
                    state = upsert_part_state(
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
                                part_id=part_id,
                            )
                        )

                    if chunks:
                        await emit_chunks(chunks)

                break
            except Exception:
                if stop_event.is_set():
                    break
                logger.exception("Codex event stream failed; retrying")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            finally:
                await flush_buffered_text_chunk()
    except Exception:
        logger.exception("Codex event stream failed")


def normalize_role(role: Any) -> str | None:
    if not isinstance(role, str):
        return None
    value = role.strip().lower()
    if not value:
        return None
    if value.startswith("role_"):
        value = value[5:]
    if value in {"assistant", "agent", "model", "ai"}:
        return "agent"
    if value in {"user", "human"}:
        return "user"
    if value == "system":
        return "system"
    return value


def extract_stream_role(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    role = part.get("role") or props.get("role")
    if role is None:
        message = props.get("message")
        if isinstance(message, Mapping):
            role = message.get("role")
    return normalize_role(role)


def extract_first_nonempty_string(
    source: Mapping[str, Any] | None,
    keys: tuple[str, ...],
) -> str | None:
    if not isinstance(source, Mapping):
        return None
    for key in keys:
        value = source.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def extract_stream_session_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    candidate = extract_first_nonempty_string(part, ("sessionID",))
    if candidate:
        return candidate
    return extract_first_nonempty_string(props, ("sessionID",))


def extract_event_session_id(event: Mapping[str, Any]) -> str | None:
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    direct = extract_first_nonempty_string(props, ("sessionID",))
    if direct:
        return direct
    info = props.get("info")
    if isinstance(info, Mapping):
        info_session_id = extract_first_nonempty_string(info, ("sessionID",))
        if info_session_id:
            return info_session_id
    part = props.get("part")
    if isinstance(part, Mapping):
        part_session_id = extract_first_nonempty_string(part, ("sessionID",))
        if part_session_id:
            return part_session_id
    return None


def extract_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized:
            result.append(normalized)
    return result


def extract_interrupt_asked_request_id(props: Mapping[str, Any]) -> str | None:
    return extract_first_nonempty_string(props, ("id",))


def extract_interrupt_resolved_request_id(props: Mapping[str, Any]) -> str | None:
    return extract_first_nonempty_string(props, ("requestID", "id"))


def extract_interrupt_asked_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    if event_type not in _INTERRUPT_ASKED_EVENT_TYPES:
        return None
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    request_id = extract_interrupt_asked_request_id(props)
    if not request_id:
        return None
    if event_type == "permission.asked":
        details: dict[str, Any] = {
            "permission": props.get("permission"),
            "patterns": extract_string_list(props.get("patterns")),
            "always": extract_string_list(props.get("always")),
        }
        codex_private: dict[str, Any] = {}
        if isinstance(props.get("metadata"), Mapping):
            codex_private["metadata"] = dict(props.get("metadata"))
        tool = props.get("tool")
        if isinstance(tool, Mapping):
            codex_private["tool"] = dict(tool)
        return {
            "request_id": request_id,
            "interrupt_type": "permission",
            "details": details,
            "codex_private": codex_private,
        }
    questions = props.get("questions")
    details = {"questions": questions if isinstance(questions, list) else []}
    codex_private = {}
    tool = props.get("tool")
    if isinstance(tool, Mapping):
        codex_private["tool"] = dict(tool)
    return {
        "request_id": request_id,
        "interrupt_type": "question",
        "details": details,
        "codex_private": codex_private,
    }


def extract_interrupt_resolved_event(event: Mapping[str, Any]) -> dict[str, str] | None:
    event_type = event.get("type")
    if event_type not in _INTERRUPT_RESOLVED_EVENT_TYPES:
        return None
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    request_id = extract_interrupt_resolved_request_id(props)
    if not request_id:
        return None
    if event_type == "permission.replied":
        return {
            "request_id": request_id,
            "event_type": event_type,
            "interrupt_type": "permission",
            "resolution": "replied",
        }
    if event_type == "question.rejected":
        return {
            "request_id": request_id,
            "event_type": event_type,
            "interrupt_type": "question",
            "resolution": "rejected",
        }
    return {
        "request_id": request_id,
        "event_type": event_type,
        "interrupt_type": "question",
        "resolution": "replied",
    }


def extract_stream_message_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    candidate = extract_first_nonempty_string(part, ("messageID",))
    if candidate:
        return candidate
    return extract_first_nonempty_string(props, ("messageID",))


def extract_stream_part_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    candidate = extract_first_nonempty_string(part, ("id",))
    if candidate:
        return candidate
    return extract_first_nonempty_string(props, ("partID",))


def extract_stream_part_type(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    for value in (
        part.get("type"),
        part.get("kind"),
        props.get("partType"),
        props.get("part_type"),
    ):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized:
                return normalized
    return None


def map_part_type_to_block_type(part_type: str | None) -> BlockType | None:
    if not part_type:
        return None
    if part_type == "text":
        return BlockType.TEXT
    if part_type in {"reasoning", "thinking", "thought"}:
        return BlockType.REASONING
    if part_type in {
        "tool",
        "tool_call",
        "toolcall",
        "function_call",
        "functioncall",
        "action",
    }:
        return BlockType.TOOL_CALL
    return None


def resolve_stream_block_type(
    part: Mapping[str, Any], props: Mapping[str, Any]
) -> BlockType | None:
    explicit_part_type = extract_stream_part_type(part, props)
    if explicit_part_type is not None:
        return map_part_type_to_block_type(explicit_part_type)
    return classify_stream_block_type(part, props)


def classify_stream_block_type(
    part: Mapping[str, Any], props: Mapping[str, Any]
) -> BlockType | None:
    candidates: list[str] = []
    for value in (
        part.get("block_type"),
        props.get("block_type"),
        part.get("channel"),
        props.get("channel"),
        part.get("kind"),
        props.get("kind"),
        props.get("type"),
        props.get("deltaType"),
        props.get("phase"),
        props.get("name"),
    ):
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip().lower())

    if any(
        any(keyword in candidate for keyword in ("reason", "thinking", "thought"))
        for candidate in candidates
    ):
        return BlockType.REASONING
    if any(
        any(
            keyword in candidate
            for keyword in (
                "tool",
                "function_call",
                "functioncall",
                "tool_call",
                "toolcall",
                "action",
            )
        )
        for candidate in candidates
    ):
        return BlockType.TOOL_CALL
    if any(
        any(keyword in candidate for keyword in ("text", "answer", "final"))
        for candidate in candidates
    ):
        return BlockType.TEXT
    return None
