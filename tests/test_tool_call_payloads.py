from __future__ import annotations

from codex_a2a_server.tool_call_payloads import (
    as_tool_call_payload,
    normalize_tool_call_payload,
    tool_call_output_delta_payload_from_notification,
    tool_call_state_payload_from_item,
    tool_call_state_payload_from_part,
)


def test_normalize_tool_call_payload_requires_explicit_kind() -> None:
    assert normalize_tool_call_payload({"tool": "bash", "status": "running"}) is None


def test_tool_call_state_payload_from_part_extracts_structured_state() -> None:
    payload = tool_call_state_payload_from_part(
        {
            "callID": "call-1",
            "tool": "bash",
            "sourceMethod": "commandExecution",
            "state": {
                "status": "completed",
                "title": "pytest",
                "output": "Passed",
            },
        }
    )

    assert as_tool_call_payload(payload) == {
        "kind": "state",
        "call_id": "call-1",
        "tool": "bash",
        "source_method": "commandExecution",
        "status": "completed",
        "title": "pytest",
        "output": "Passed",
    }


def test_tool_call_output_delta_payload_preserves_verbatim_text() -> None:
    payload = tool_call_output_delta_payload_from_notification(
        source_method="commandExecution",
        delta=".\n",
        call_id="call-1",
        tool="bash",
        status="running",
    )

    assert as_tool_call_payload(payload) == {
        "kind": "output_delta",
        "source_method": "commandExecution",
        "call_id": "call-1",
        "tool": "bash",
        "status": "running",
        "output_delta": ".\n",
    }


def test_normalize_tool_call_payload_accepts_a2a_style_aliases() -> None:
    payload = normalize_tool_call_payload(
        {
            "kind": "output_delta",
            "sourceMethod": "commandExecution",
            "callId": "call-1",
            "tool": "bash",
            "status": "running",
            "outputDelta": "Passed\n",
        }
    )

    assert as_tool_call_payload(payload) == {
        "kind": "output_delta",
        "source_method": "commandExecution",
        "call_id": "call-1",
        "tool": "bash",
        "status": "running",
        "output_delta": "Passed\n",
    }


def test_tool_call_state_payload_from_item_normalizes_command_execution_lifecycle() -> None:
    payload = tool_call_state_payload_from_item(
        {
            "type": "commandExecution",
            "id": "call-1",
            "status": "inProgress",
            "command": "/bin/bash -lc pytest",
            "cwd": "/workspace",
            "aggregatedOutput": "Passed\n",
            "exitCode": 0,
            "durationMs": 12,
        }
    )

    assert as_tool_call_payload(payload) == {
        "kind": "state",
        "source_method": "commandExecution",
        "call_id": "call-1",
        "status": "running",
        "input": {
            "command": "/bin/bash -lc pytest",
            "cwd": "/workspace",
        },
        "output": {
            "text": "Passed\n",
            "exit_code": 0,
            "duration_ms": 12,
        },
    }


def test_tool_call_state_payload_from_item_summarizes_file_change_paths() -> None:
    payload = tool_call_state_payload_from_item(
        {
            "type": "fileChange",
            "id": "call-file-1",
            "status": "completed",
            "changes": [
                {"path": "/workspace/src/app.py", "kind": {"type": "edit"}},
                {"path": "/workspace/tests/test_app.py", "kind": {"type": "edit"}},
            ],
        }
    )

    assert as_tool_call_payload(payload) == {
        "kind": "state",
        "source_method": "fileChange",
        "call_id": "call-file-1",
        "status": "completed",
        "input": {
            "paths": [
                "/workspace/src/app.py",
                "/workspace/tests/test_app.py",
            ],
            "change_count": 2,
        },
    }
