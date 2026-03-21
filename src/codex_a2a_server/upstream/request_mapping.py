from __future__ import annotations

import shlex
from typing import Any


def convert_request_parts_to_turn_input(request: dict[str, Any]) -> list[dict[str, Any]]:
    parts = request.get("parts")
    if not isinstance(parts, list):
        raise RuntimeError("request.parts must be an array")
    converted: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            raise RuntimeError("request.parts items must be objects")
        part_type = part.get("type")
        if part_type != "text":
            raise RuntimeError("Only text request.parts are currently supported")
        text = part.get("text")
        if not isinstance(text, str):
            raise RuntimeError("request.parts[].text must be a string")
        converted.append({"type": "text", "text": text, "text_elements": []})
    return converted


def format_shell_response(result: dict[str, Any]) -> str:
    exit_code = result.get("exitCode")
    stdout = result.get("stdout")
    stderr = result.get("stderr")
    lines: list[str] = [f"exit_code: {exit_code}"]
    if isinstance(stdout, str) and stdout:
        lines.append("stdout:")
        lines.append(stdout.rstrip())
    if isinstance(stderr, str) and stderr:
        lines.append("stderr:")
        lines.append(stderr.rstrip())
    return "\n".join(lines)


def build_shell_exec_params(
    *,
    command_text: str,
    directory: str | None,
    default_workspace_root: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"command": shlex.split(command_text)}
    if directory:
        params["cwd"] = directory
    elif default_workspace_root:
        params["cwd"] = default_workspace_root
    return params


def uuid_like_suffix(value: str) -> str:
    normalized = value.strip().replace(" ", "-")
    if not normalized:
        return "empty"
    return normalized[:32]
