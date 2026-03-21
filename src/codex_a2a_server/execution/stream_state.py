from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from a2a.types import DataPart, TextPart

from codex_a2a_server.contracts.runtime_output import (
    build_stream_artifact_metadata as build_runtime_stream_metadata,
)
from codex_a2a_server.execution.output_mapping import merge_token_usage

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

    def ingest_token_usage(self, usage: dict[str, Any] | None) -> None:
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
    return build_runtime_stream_metadata(
        block_type=block_type.value,
        source=source,
        message_id=message_id,
        role=role,
        sequence=sequence,
        event_id=event_id,
    )
