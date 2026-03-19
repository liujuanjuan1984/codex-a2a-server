from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_INTERRUPT_ASKED_EVENT_TYPES = {"permission.asked", "question.asked"}
_INTERRUPT_RESOLVED_EVENT_TYPES = {"permission.replied", "question.replied", "question.rejected"}


def _normalized_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


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
    display_message = _normalized_string(props.get("display_message"))
    if display_message is not None:
        details["display_message"] = display_message
    return details


def extract_interrupt_questions(props: Mapping[str, Any]) -> list[Any]:
    questions = props.get("questions")
    if isinstance(questions, list):
        return questions
    return []


def diagnose_interrupt_event(event: Mapping[str, Any]) -> str | None:
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None
    if event_type in _INTERRUPT_ASKED_EVENT_TYPES:
        props = event.get("properties")
        if not isinstance(props, Mapping):
            return "interrupt asked event missing properties mapping"
        request_id = props.get("id")
        if not isinstance(request_id, str) or not request_id.strip():
            return "interrupt asked event missing request id"
        return None
    if event_type in _INTERRUPT_RESOLVED_EVENT_TYPES:
        props = event.get("properties")
        if not isinstance(props, Mapping):
            return "interrupt resolved event missing properties mapping"
        request_id = props.get("requestID") or props.get("id")
        if not isinstance(request_id, str) or not request_id.strip():
            return "interrupt resolved event missing request id"
        return None
    if event_type.startswith("permission.") or event_type.startswith("question."):
        return f"unsupported interrupt event type: {event_type}"
    return None


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
        metadata = props.get("metadata")
        if isinstance(metadata, Mapping):
            codex_private["metadata"] = dict(metadata)
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
    question_private: dict[str, Any] = {}
    metadata = props.get("metadata")
    if isinstance(metadata, Mapping):
        question_private["metadata"] = dict(metadata)
    tool = props.get("tool")
    if isinstance(tool, Mapping):
        question_private["tool"] = dict(tool)
    return {
        "request_id": normalized_request_id,
        "interrupt_type": "question",
        "details": details,
        "codex_private": question_private,
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
