from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from a2a.server.agent_execution import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Artifact,
    DataPart,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TextPart,
)

from .runtime_output_contracts import build_output_metadata as build_runtime_output_metadata


def build_assistant_message(
    task_id: str,
    context_id: str,
    text: str,
    *,
    message_id: str | None = None,
) -> Message:
    return Message(
        message_id=message_id or str(uuid.uuid4()),
        role=Role.agent,
        parts=[Part(root=TextPart(text=text))],
        task_id=task_id,
        context_id=context_id,
    )


async def enqueue_artifact_update(
    *,
    event_queue: EventQueue,
    task_id: str,
    context_id: str,
    artifact_id: str,
    part: TextPart | DataPart,
    append: bool | None,
    last_chunk: bool | None,
    artifact_metadata: Mapping[str, Any] | None = None,
    event_metadata: Mapping[str, Any] | None = None,
) -> None:
    normalized_last_chunk = True if last_chunk is True else None
    artifact = Artifact(
        artifact_id=artifact_id,
        parts=[Part(root=part)],
        metadata=dict(artifact_metadata) if artifact_metadata else None,
    )
    await event_queue.enqueue_event(
        TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            artifact=artifact,
            append=append,
            last_chunk=normalized_last_chunk,
            metadata=dict(event_metadata) if event_metadata else None,
        )
    )


def build_output_metadata(
    *,
    session_id: str | None = None,
    session_title: str | None = None,
    usage: Mapping[str, Any] | None = None,
    stream: Mapping[str, Any] | None = None,
    interrupt: Mapping[str, Any] | None = None,
    codex_private: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    return build_runtime_output_metadata(
        session_id=session_id,
        session_title=session_title,
        usage=usage,
        stream=stream,
        interrupt=interrupt,
        codex_private=codex_private,
    )


def extract_token_usage(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None

    candidates: list[Mapping[str, Any]] = []
    info = payload.get("info")
    if isinstance(info, Mapping):
        candidates.append(info)

    props = payload.get("properties")
    if isinstance(props, Mapping):
        props_info = props.get("info")
        if isinstance(props_info, Mapping):
            candidates.append(props_info)
        part = props.get("part")
        if isinstance(part, Mapping):
            candidates.append(part)

    for candidate in candidates:
        usage = _extract_usage_from_info_like(candidate)
        if usage is not None:
            return usage
    return None


def merge_token_usage(
    base: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if base is None and incoming is None:
        return None
    merged: dict[str, Any] = dict(base) if base else {}
    if incoming:
        for key, value in incoming.items():
            if value is None:
                continue
            if key == "raw" and isinstance(value, Mapping):
                existing = merged.get("raw")
                if isinstance(existing, Mapping):
                    merged["raw"] = {**dict(existing), **dict(value)}
                else:
                    merged["raw"] = dict(value)
                continue
            merged[key] = value
    return merged or None


def build_history(context: RequestContext) -> list[Message]:
    if context.current_task and context.current_task.history:
        history = list(context.current_task.history)
    else:
        history = []
        if context.message:
            history.append(context.message)
    return history


def _coerce_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        if "." in normalized or "e" in normalized.lower():
            parsed = float(normalized)
            if parsed.is_integer():
                return int(parsed)
            return parsed
        return int(normalized)
    except ValueError:
        return None


def _extract_usage_from_info_like(info: Mapping[str, Any]) -> dict[str, Any] | None:
    tokens = info.get("tokens")
    if not isinstance(tokens, Mapping):
        return None

    usage: dict[str, Any] = {}
    raw: dict[str, Any] = {"tokens": dict(tokens)}

    input_tokens = _coerce_number(tokens.get("input"))
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens

    output_tokens = _coerce_number(tokens.get("output"))
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens

    total_tokens = _coerce_number(tokens.get("total"))
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens
    elif input_tokens is not None and output_tokens is not None:
        usage["total_tokens"] = input_tokens + output_tokens

    reasoning_tokens = _coerce_number(tokens.get("reasoning"))
    if reasoning_tokens is not None:
        usage["reasoning_tokens"] = reasoning_tokens

    cache = tokens.get("cache")
    if isinstance(cache, Mapping):
        cache_usage: dict[str, Any] = {}
        cache_read = _coerce_number(cache.get("read"))
        if cache_read is not None:
            cache_usage["read_tokens"] = cache_read
        cache_write = _coerce_number(cache.get("write"))
        if cache_write is not None:
            cache_usage["write_tokens"] = cache_write
        if cache_usage:
            usage["cache_tokens"] = cache_usage

    cost = _coerce_number(info.get("cost"))
    if cost is not None:
        usage["cost"] = cost
        raw["cost"] = cost

    if not usage:
        return None
    usage["raw"] = raw
    return usage
