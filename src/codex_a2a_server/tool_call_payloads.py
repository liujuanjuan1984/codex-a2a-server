from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal, TypeAlias

from a2a._base import A2ABaseModel
from pydantic import AliasChoices, Field, ValidationError, field_validator

ToolCallSourceMethod = Literal["commandExecution", "fileChange"]


def _normalized_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalized_status(value: Any) -> str | None:
    normalized = _normalized_optional_string(value)
    if normalized is None:
        return None
    aliases = {
        "inProgress": "running",
        "in_progress": "running",
        "running": "running",
        "completed": "completed",
        "failed": "failed",
        "error": "failed",
        "errored": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "pending": "pending",
    }
    return aliases.get(normalized, normalized)


def _source_method_from_item_type(value: Any) -> ToolCallSourceMethod | None:
    normalized = _normalized_optional_string(value)
    if normalized == "commandExecution":
        return "commandExecution"
    if normalized == "fileChange":
        return "fileChange"
    return None


class ToolCallStatePayload(A2ABaseModel):
    kind: Literal["state"] = "state"
    source_method: ToolCallSourceMethod | None = Field(
        default=None,
        validation_alias=AliasChoices("source_method", "sourceMethod"),
    )
    call_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("call_id", "callId", "callID"),
    )
    tool: str | None = None
    status: str | None = None
    title: Any | None = None
    subtitle: Any | None = None
    input: Any | None = None
    output: Any | None = None
    error: Any | None = None

    @field_validator("call_id", "tool", mode="before")
    @classmethod
    def _strip_text_fields(cls, value: Any) -> str | None:
        return _normalized_optional_string(value)

    @field_validator("source_method", mode="before")
    @classmethod
    def _normalize_source_method(cls, value: Any) -> ToolCallSourceMethod | None:
        return _source_method_from_item_type(value)

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status_field(cls, value: Any) -> str | None:
        return _normalized_status(value)


class ToolCallOutputDeltaPayload(A2ABaseModel):
    kind: Literal["output_delta"] = "output_delta"
    source_method: ToolCallSourceMethod | None = Field(
        default=None,
        validation_alias=AliasChoices("source_method", "sourceMethod"),
    )
    call_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("call_id", "callId", "callID"),
    )
    tool: str | None = None
    status: str | None = None
    output_delta: str = Field(
        ...,
        validation_alias=AliasChoices("output_delta", "outputDelta"),
    )

    @field_validator("call_id", "tool", mode="before")
    @classmethod
    def _strip_text_fields(cls, value: Any) -> str | None:
        return _normalized_optional_string(value)

    @field_validator("source_method", mode="before")
    @classmethod
    def _normalize_source_method(cls, value: Any) -> ToolCallSourceMethod | None:
        return _source_method_from_item_type(value)

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status_field(cls, value: Any) -> str | None:
        return _normalized_status(value)

    @field_validator("output_delta", mode="before")
    @classmethod
    def _preserve_verbatim_output_delta(cls, value: Any) -> str:
        if not isinstance(value, str) or value == "":
            raise ValueError("output_delta must be a non-empty string")
        return value


ToolCallPayload: TypeAlias = ToolCallStatePayload | ToolCallOutputDeltaPayload


def serialize_tool_call_payload(payload: ToolCallPayload) -> str:
    return json.dumps(
        as_tool_call_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def normalize_tool_call_payload(payload: Mapping[str, Any]) -> ToolCallPayload | None:
    kind = _normalized_optional_string(payload.get("kind"))
    if kind == "state":
        return _build_state_payload(payload)
    if kind == "output_delta":
        return _build_output_delta_payload(payload)
    return None


def tool_call_state_payload_from_part(part: Mapping[str, Any]) -> ToolCallStatePayload | None:
    state = part.get("state")
    payload: dict[str, Any] = {"kind": "state"}

    call_id = _normalized_optional_string(
        part.get("callID") or part.get("callId") or part.get("call_id")
    )
    if call_id is not None:
        payload["call_id"] = call_id

    tool = _normalized_optional_string(part.get("tool") or part.get("name"))
    if tool is not None:
        payload["tool"] = tool

    source_method = _normalized_optional_string(
        part.get("sourceMethod") or part.get("source_method")
    )
    if source_method is not None:
        payload["source_method"] = source_method

    if isinstance(state, Mapping):
        status = _normalized_optional_string(state.get("status"))
        if status is not None:
            payload["status"] = status
        for key in ("title", "subtitle", "input", "output", "error"):
            value = state.get(key)
            if value is not None:
                payload[key] = value

    if len(payload) == 1:
        return None
    return _build_state_payload(payload)


def tool_call_state_payload_from_item(item: Mapping[str, Any]) -> ToolCallStatePayload | None:
    source_method = _source_method_from_item_type(item.get("type"))
    call_id = _normalized_optional_string(item.get("id"))
    if source_method is None or call_id is None:
        return None

    payload: dict[str, Any] = {
        "kind": "state",
        "source_method": source_method,
        "call_id": call_id,
        "status": item.get("status"),
    }

    if source_method == "commandExecution":
        command = _normalized_optional_string(item.get("command"))
        cwd = _normalized_optional_string(item.get("cwd"))
        if command is not None or cwd is not None:
            command_input: dict[str, Any] = {}
            if command is not None:
                command_input["command"] = command
            if cwd is not None:
                command_input["cwd"] = cwd
            payload["input"] = command_input

        aggregated_output = item.get("aggregatedOutput")
        exit_code = item.get("exitCode")
        duration_ms = item.get("durationMs")
        if (
            isinstance(aggregated_output, str)
            and aggregated_output != ""
            or exit_code is not None
            or duration_ms is not None
        ):
            command_output: dict[str, Any] = {}
            if isinstance(aggregated_output, str) and aggregated_output != "":
                command_output["text"] = aggregated_output
            if exit_code is not None:
                command_output["exit_code"] = exit_code
            if duration_ms is not None:
                command_output["duration_ms"] = duration_ms
            payload["output"] = command_output

    if source_method == "fileChange":
        changes = item.get("changes")
        if isinstance(changes, list):
            paths = [
                path
                for change in changes
                if isinstance(change, Mapping)
                for path in [_normalized_optional_string(change.get("path"))]
                if path is not None
            ]
            if paths:
                payload["input"] = {
                    "paths": paths,
                    "change_count": len(paths),
                }

    error = item.get("error")
    if error is not None:
        payload["error"] = error

    return _build_state_payload(payload)


def tool_call_output_delta_payload_from_notification(
    *,
    source_method: ToolCallSourceMethod,
    delta: str,
    call_id: str | None = None,
    tool: str | None = None,
    status: str | None = None,
) -> ToolCallOutputDeltaPayload | None:
    if delta == "":
        return None

    payload: dict[str, Any] = {
        "kind": "output_delta",
        "source_method": source_method,
        "output_delta": delta,
    }
    normalized_call_id = _normalized_optional_string(call_id)
    if normalized_call_id is not None:
        payload["call_id"] = normalized_call_id
    if tool is not None:
        payload["tool"] = tool
    if status is not None:
        payload["status"] = status
    return _build_output_delta_payload(payload)


def _build_state_payload(payload: Mapping[str, Any]) -> ToolCallStatePayload | None:
    normalized = ToolCallStatePayload.model_validate(payload)
    if len(as_tool_call_payload(normalized)) == 1:
        return None
    return normalized


def _build_output_delta_payload(
    payload: Mapping[str, Any],
) -> ToolCallOutputDeltaPayload | None:
    try:
        return ToolCallOutputDeltaPayload.model_validate(payload)
    except ValidationError:
        return None


def as_tool_call_payload(payload: ToolCallPayload) -> dict[str, Any]:
    return payload.model_dump(mode="json", by_alias=False, exclude_none=True)
