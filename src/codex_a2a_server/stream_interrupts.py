from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_INTERRUPT_ASKED_EVENT_TYPES = {"permission.asked", "question.asked"}
_INTERRUPT_RESOLVED_EVENT_TYPES = {"permission.replied", "question.replied", "question.rejected"}
_INTERRUPT_TEXT_FIELD_KEYS = (
    "message",
    "description",
    "reason",
    "prompt",
    "display_message",
    "displayMessage",
)
_INTERRUPT_NESTED_DETAIL_KEYS = ("request", "context", "prompt")
_INTERRUPT_DISPLAY_MESSAGE_NESTED_PATHS = (
    ("request", "message"),
    ("request", "description"),
    ("request", "prompt"),
    ("request", "reason"),
    ("context", "message"),
    ("context", "description"),
    ("context", "prompt"),
    ("context", "reason"),
    ("prompt", "message"),
    ("prompt", "description"),
)
_INTERRUPT_QUESTION_LIST_PATHS = (
    ("questions",),
    ("request", "questions"),
    ("context", "questions"),
    ("prompt", "questions"),
)


def _normalized_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _resolve_nested_value(payload: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _first_nested_string(payload: Mapping[str, Any], *paths: tuple[str, ...]) -> str | None:
    for path in paths:
        value = _normalized_string(_resolve_nested_value(payload, path))
        if value is not None:
            return value
    return None


def _first_list(payload: Mapping[str, Any], *paths: tuple[str, ...]) -> list[Any]:
    for path in paths:
        value = _resolve_nested_value(payload, path)
        if isinstance(value, list):
            return value
    return []


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


def extract_interrupt_text_details(props: Mapping[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for key in _INTERRUPT_TEXT_FIELD_KEYS:
        value = _normalized_string(props.get(key))
        if value is not None:
            details[key] = value
    for key in _INTERRUPT_NESTED_DETAIL_KEYS:
        value = props.get(key)
        if isinstance(value, Mapping):
            details[key] = dict(value)
    display_message = (
        _normalized_string(props.get("display_message"))
        or _normalized_string(props.get("displayMessage"))
        or _normalized_string(props.get("message"))
        or _normalized_string(props.get("description"))
        or _normalized_string(props.get("prompt"))
        or _normalized_string(props.get("reason"))
        or _first_nested_string(props, *_INTERRUPT_DISPLAY_MESSAGE_NESTED_PATHS)
    )
    if display_message is not None:
        details["display_message"] = display_message
    return details


def extract_interrupt_questions(props: Mapping[str, Any]) -> list[Any]:
    return _first_list(props, *_INTERRUPT_QUESTION_LIST_PATHS)


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
        details.update(extract_interrupt_text_details(props))
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
    details = {"questions": extract_interrupt_questions(props)}
    details.update(extract_interrupt_text_details(props))
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
