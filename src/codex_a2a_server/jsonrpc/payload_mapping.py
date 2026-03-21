from __future__ import annotations

from typing import Any

from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus, TextPart

from codex_a2a_server.parts.text import extract_text_from_parts


def session_context_id(session_id: str) -> str:
    return session_id


def extract_session_title(session: dict[str, Any]) -> str:
    title = session.get("title")
    if not isinstance(title, str):
        return ""
    return title.strip()


def as_a2a_session_task(session: Any) -> dict[str, Any] | None:
    if not isinstance(session, dict):
        return None
    raw_id = session.get("id")
    if not isinstance(raw_id, str):
        return None
    session_id = raw_id.strip()
    if not session_id:
        return None
    title = extract_session_title(session)
    if not title:
        return None
    task = Task(
        id=session_id,
        context_id=session_context_id(session_id),
        status=TaskStatus(state=TaskState.completed),
        metadata={
            "shared": {"session": {"id": session_id, "title": title}},
            "codex": {"raw": session},
        },
    )
    return task.model_dump(by_alias=True, exclude_none=True)


def as_a2a_message(session_id: str, item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    info = item.get("info")
    if not isinstance(info, dict):
        return None
    raw_id = info.get("id")
    if not isinstance(raw_id, str):
        return None
    message_id = raw_id.strip()
    if not message_id:
        return None

    role_raw = info.get("role")
    role = Role.agent
    if isinstance(role_raw, str) and role_raw.strip().lower() == "user":
        role = Role.user

    text = extract_text_from_parts(item.get("parts"))

    message = Message(
        message_id=message_id,
        role=role,
        parts=[Part(root=TextPart(text=text))],
        context_id=session_context_id(session_id),
        metadata={
            "shared": {"session": {"id": session_id}},
            "codex": {"raw": item},
        },
    )
    return message.model_dump(by_alias=True, exclude_none=True)


def message_to_item(message: Any) -> dict[str, Any]:
    if hasattr(message, "message_id") and hasattr(message, "text"):
        return {
            "info": {
                "id": getattr(message, "message_id", None) or "msg-shell",
                "role": "assistant",
            },
            "parts": [{"type": "text", "text": getattr(message, "text", "")}],
            "raw": getattr(message, "raw", {}),
        }
    if isinstance(message, dict):
        return message
    raise ValueError("Unsupported session control response payload")


def extract_raw_items(raw_result: Any, *, kind: str) -> list[Any]:
    if isinstance(raw_result, list):
        return raw_result
    raise ValueError(f"Codex {kind} payload must be an array; got {type(raw_result).__name__}")
