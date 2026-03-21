from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from codex_a2a_server.execution.tool_call_payloads import (
    ToolCallSourceMethod,
    as_tool_call_payload,
    tool_call_output_delta_payload_from_notification,
    tool_call_state_payload_from_item,
)


def _normalized_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _normalized_string(payload.get(key))
        if value is not None:
            return value
    return None


def _extract_tool_status(payload: dict[str, Any]) -> str | None:
    value = _first_string(payload, "status")
    if value is not None:
        return value
    state = payload.get("state")
    if isinstance(state, dict):
        return _first_string(state, "status")
    return None


def _tool_source_method(method: str) -> ToolCallSourceMethod | None:
    parts = method.split("/")
    if len(parts) < 2:
        return None
    normalized = _normalized_string(parts[1])
    if normalized == "commandExecution":
        return "commandExecution"
    if normalized == "fileChange":
        return "fileChange"
    return None


def build_tool_call_output_event(method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    thread_id = _first_string(params, "threadId")
    delta = params.get("delta")
    if thread_id is None or not isinstance(delta, str) or delta == "":
        return None

    explicit_call_id = _first_string(params, "callID", "callId", "call_id")
    item_id = _first_string(params, "itemId")
    call_id = explicit_call_id or item_id
    part_id = item_id or call_id
    if part_id is None:
        return None

    tool = _first_string(params, "tool", "name")
    status = _extract_tool_status(params)
    source_method = _tool_source_method(method)
    if source_method is None:
        return None
    payload = tool_call_output_delta_payload_from_notification(
        source_method=source_method,
        delta=delta,
        call_id=call_id,
        tool=tool,
        status=status,
    )
    if payload is None:
        return None

    part: dict[str, Any] = {
        "sessionID": thread_id,
        "id": part_id,
        "type": "tool_call",
        "role": "assistant",
    }
    if item_id is not None:
        part["messageID"] = item_id
    if call_id is not None:
        part["callID"] = call_id
    if tool is not None:
        part["tool"] = tool
    if status is not None:
        part["state"] = {"status": status}
    if source_method is not None:
        part["sourceMethod"] = source_method

    return {
        "type": "message.part.updated",
        "properties": {
            "part": part,
            "delta": as_tool_call_payload(payload),
        },
    }


def build_tool_call_state_event(params: dict[str, Any]) -> dict[str, Any] | None:
    thread_id = _first_string(params, "threadId")
    item = params.get("item")
    if thread_id is None or not isinstance(item, dict):
        return None

    payload = tool_call_state_payload_from_item(item)
    if payload is None:
        return None

    part_id = _first_string(item, "id")
    if part_id is None:
        return None

    payload_data = as_tool_call_payload(payload)
    state_payload: dict[str, Any] = {}
    for key in ("status", "title", "subtitle", "input", "output", "error"):
        value = payload_data.get(key)
        if value is not None:
            state_payload[key] = value

    part: dict[str, Any] = {
        "sessionID": thread_id,
        "messageID": part_id,
        "id": part_id,
        "type": "tool_call",
        "role": "assistant",
    }
    call_id = payload_data.get("call_id")
    if isinstance(call_id, str) and call_id:
        part["callID"] = call_id
    tool = payload_data.get("tool")
    if isinstance(tool, str) and tool:
        part["tool"] = tool
    source_method = payload_data.get("source_method")
    if isinstance(source_method, str) and source_method:
        part["sourceMethod"] = source_method
    if state_payload:
        part["state"] = state_payload

    return {
        "type": "message.part.updated",
        "properties": {
            "part": part,
            "delta": payload_data,
        },
    }
