from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from a2a.types import DataPart, TextPart

from codex_a2a_server.execution.stream_state import (
    BlockType,
    NormalizedStreamChunk,
    StreamPartState,
)
from codex_a2a_server.execution.tool_call_payloads import (
    ToolCallPayload,
    as_tool_call_payload,
    normalize_tool_call_payload,
    serialize_tool_call_payload,
    tool_call_state_payload_from_part,
)

logger = logging.getLogger(__name__)


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


def _extract_first_nonempty_string_from_sources(
    *sources: tuple[Mapping[str, Any] | None, tuple[str, ...]],
) -> str | None:
    for source, keys in sources:
        candidate = extract_first_nonempty_string(source, keys)
        if candidate:
            return candidate
    return None


def _extract_mapping(source: Mapping[str, Any] | None, key: str) -> Mapping[str, Any] | None:
    if not isinstance(source, Mapping):
        return None
    value = source.get(key)
    if isinstance(value, Mapping):
        return value
    return None


def extract_stream_session_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    return _extract_first_nonempty_string_from_sources(
        (part, ("sessionID",)),
        (props, ("sessionID",)),
    )


def extract_event_session_id(event: Mapping[str, Any]) -> str | None:
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    return _extract_first_nonempty_string_from_sources(
        (props, ("sessionID",)),
        (_extract_mapping(props, "info"), ("sessionID",)),
        (_extract_mapping(props, "part"), ("sessionID",)),
    )


def extract_stream_message_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    return _extract_first_nonempty_string_from_sources(
        (part, ("messageID",)),
        (props, ("messageID",)),
    )


def extract_stream_part_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    return _extract_first_nonempty_string_from_sources(
        (part, ("id",)),
        (props, ("partID",)),
    )


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


def upsert_stream_part_state(
    *,
    part_states: dict[str, StreamPartState],
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
    task_id: str,
    session_id: str,
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
        state.part_id,
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
    task_id: str,
    session_id: str,
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
