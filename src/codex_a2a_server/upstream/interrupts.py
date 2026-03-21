from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _normalized_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _mapping_value(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _first_nested_string(payload: Mapping[str, Any], *paths: tuple[str, ...]) -> str | None:
    for path in paths:
        current: Any = payload
        for key in path:
            if not isinstance(current, Mapping):
                break
            current = current.get(key)
        else:
            value = _normalized_string(current)
            if value is not None:
                return value
    return None


def _extract_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = _normalized_string(item)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        values.append(normalized)
    return values


def _extract_permission_patterns(params: dict[str, Any]) -> list[str]:
    patterns = _extract_string_list(params.get("patterns"))
    if patterns:
        return patterns

    parsed_cmd = params.get("parsedCmd")
    if not isinstance(parsed_cmd, list):
        return []

    resolved_patterns: list[str] = []
    seen: set[str] = set()
    for entry in parsed_cmd:
        if not isinstance(entry, Mapping):
            continue
        path = _normalized_string(entry.get("path"))
        if path is None or path in seen:
            continue
        seen.add(path)
        resolved_patterns.append(path)
    return resolved_patterns


def _extract_question_properties_questions(params: dict[str, Any]) -> list[Any]:
    questions = params.get("questions")
    if isinstance(questions, list):
        return questions

    context = _mapping_value(params.get("context"))
    if context is not None and isinstance(context.get("questions"), list):
        return context["questions"]
    return []


class InterruptRequestError(RuntimeError):
    def __init__(
        self,
        *,
        error_type: str,
        request_id: str,
        expected_interrupt_type: str | None = None,
        actual_interrupt_type: str | None = None,
    ) -> None:
        super().__init__(error_type)
        self.error_type = error_type
        self.request_id = request_id
        self.expected_interrupt_type = expected_interrupt_type
        self.actual_interrupt_type = actual_interrupt_type


@dataclass(frozen=True)
class InterruptRequestBinding:
    request_id: str
    interrupt_type: str
    session_id: str
    created_at: float


@dataclass
class _PendingInterruptRequest:
    binding: InterruptRequestBinding
    rpc_request_id: str | int
    params: dict[str, Any]


def build_codex_permission_interrupt_properties(
    *, request_key: str, session_id: str, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "id": request_key,
        "sessionID": session_id,
        "metadata": {"method": method, "raw": params},
    }
    display_message = _first_nested_string(
        params,
        ("request", "description"),
        ("description",),
        ("reason",),
        ("request", "reason"),
    )
    if display_message is not None:
        properties["display_message"] = display_message
    patterns = _extract_permission_patterns(params)
    if patterns:
        properties["patterns"] = patterns
    always = _extract_string_list(params.get("always"))
    if always:
        properties["always"] = always
    return properties


def build_codex_question_interrupt_properties(
    *, request_key: str, session_id: str, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    properties = {
        "id": request_key,
        "sessionID": session_id,
        "questions": _extract_question_properties_questions(params),
        "metadata": {"method": method, "raw": params},
    }
    display_message = _first_nested_string(
        params,
        ("description",),
        ("context", "description"),
        ("prompt",),
    )
    if display_message is not None:
        properties["display_message"] = display_message
    return properties


def interrupt_request_status(
    binding: InterruptRequestBinding,
    *,
    interrupt_request_ttl_seconds: int,
) -> str:
    expires_at = binding.created_at + float(interrupt_request_ttl_seconds)
    if expires_at <= time.monotonic():
        return "expired"
    return "active"
