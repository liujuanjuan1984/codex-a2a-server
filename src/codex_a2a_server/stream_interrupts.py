from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_INTERRUPT_ASKED_EVENT_TYPES = {"permission.asked", "question.asked"}
_INTERRUPT_RESOLVED_EVENT_TYPES = {"permission.replied", "question.replied", "question.rejected"}


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


def extract_interrupt_asked_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    if event_type not in _INTERRUPT_ASKED_EVENT_TYPES:
        return None
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    request_id = props.get("id")
    if not isinstance(request_id, str):
        return None
    normalized_request_id = request_id.strip()
    if not normalized_request_id:
        return None
    if event_type == "permission.asked":
        details: dict[str, Any] = {
            "permission": props.get("permission"),
            "patterns": extract_string_list(props.get("patterns")),
            "always": extract_string_list(props.get("always")),
        }
        for key in (
            "message",
            "description",
            "reason",
            "prompt",
            "display_message",
            "displayMessage",
        ):
            value = props.get(key)
            if isinstance(value, str) and value.strip():
                details[key] = value.strip()
        codex_private: dict[str, Any] = {}
        if isinstance(props.get("metadata"), Mapping):
            codex_private["metadata"] = dict(props.get("metadata"))
        tool = props.get("tool")
        if isinstance(tool, Mapping):
            codex_private["tool"] = dict(tool)
        return {
            "request_id": normalized_request_id,
            "interrupt_type": "permission",
            "details": details,
            "codex_private": codex_private,
        }
    questions = props.get("questions")
    details = {"questions": questions if isinstance(questions, list) else []}
    for key in ("message", "description", "reason", "prompt", "display_message", "displayMessage"):
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            details[key] = value.strip()
    codex_private = {}
    tool = props.get("tool")
    if isinstance(tool, Mapping):
        codex_private["tool"] = dict(tool)
    return {
        "request_id": normalized_request_id,
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
    request_id = props.get("requestID") or props.get("id")
    if not isinstance(request_id, str):
        return None
    normalized_request_id = request_id.strip()
    if not normalized_request_id:
        return None
    if event_type == "permission.replied":
        return {
            "request_id": normalized_request_id,
            "event_type": event_type,
            "interrupt_type": "permission",
            "resolution": "replied",
        }
    if event_type == "question.rejected":
        return {
            "request_id": normalized_request_id,
            "event_type": event_type,
            "interrupt_type": "question",
            "resolution": "rejected",
        }
    return {
        "request_id": normalized_request_id,
        "event_type": event_type,
        "interrupt_type": "question",
        "resolution": "replied",
    }
