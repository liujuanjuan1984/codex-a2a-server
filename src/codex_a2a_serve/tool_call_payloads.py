from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal, TypeAlias, cast

from a2a._base import A2ABaseModel
from pydantic import AliasChoices, Field, ValidationError, field_validator

ToolCallKind = Literal["state", "output_delta"]
ToolCallSourceMethod = Literal["commandExecution", "fileChange"]


def _normalized_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


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

    @field_validator("call_id", "tool", "status", mode="before")
    @classmethod
    def _strip_text_fields(cls, value: Any) -> str | None:
        return _normalized_optional_string(value)

    @field_validator("source_method", mode="before")
    @classmethod
    def _normalize_source_method(cls, value: Any) -> ToolCallSourceMethod | None:
        normalized = _normalized_optional_string(value)
        if normalized in {"commandExecution", "fileChange"}:
            return cast(ToolCallSourceMethod, normalized)
        return None


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

    @field_validator("call_id", "tool", "status", mode="before")
    @classmethod
    def _strip_text_fields(cls, value: Any) -> str | None:
        return _normalized_optional_string(value)

    @field_validator("source_method", mode="before")
    @classmethod
    def _normalize_source_method(cls, value: Any) -> ToolCallSourceMethod | None:
        normalized = _normalized_optional_string(value)
        if normalized in {"commandExecution", "fileChange"}:
            return cast(ToolCallSourceMethod, normalized)
        return None

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
    if call_id is not None:
        payload["call_id"] = call_id
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
    return cast(
        dict[str, Any],
        payload.model_dump(mode="json", by_alias=False, exclude_none=True),
    )
