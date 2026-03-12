from __future__ import annotations

from typing import Any

SESSION_QUERY_METHODS: dict[str, str] = {
    "list_sessions": "codex.sessions.list",
    "get_session_messages": "codex.sessions.messages.list",
    "prompt_async": "codex.sessions.prompt_async",
    "command": "codex.sessions.command",
    "shell": "codex.sessions.shell",
}

SESSION_CONTROL_METHODS: dict[str, str] = {
    key: SESSION_QUERY_METHODS[key] for key in ("prompt_async", "command", "shell")
}

INTERRUPT_CALLBACK_METHODS: dict[str, str] = {
    "reply_permission": "codex.permission.reply",
    "reply_question": "codex.question.reply",
    "reject_question": "codex.question.reject",
}

PROMPT_ASYNC_ALLOWED_FIELDS: tuple[str, ...] = (
    "parts",
    "messageID",
    "agent",
    "system",
    "variant",
)
COMMAND_ALLOWED_FIELDS: tuple[str, ...] = (
    "command",
    "arguments",
    "messageID",
)
SHELL_ALLOWED_FIELDS: tuple[str, ...] = ("command",)


def build_session_query_extension_params(
    *,
    deployment_context: dict[str, str | bool],
) -> dict[str, Any]:
    return {
        "methods": dict(SESSION_QUERY_METHODS),
        "control_methods": dict(SESSION_CONTROL_METHODS),
        "shared_workspace_across_consumers": True,
        "tenant_isolation": "none",
        "deployment_context": deployment_context,
        "supported_metadata": ["codex.directory"],
        "result_envelope": {
            "by_method": {
                SESSION_QUERY_METHODS["list_sessions"]: {"fields": ["items"]},
                SESSION_QUERY_METHODS["get_session_messages"]: {"fields": ["items"]},
                SESSION_QUERY_METHODS["prompt_async"]: {"fields": ["ok", "session_id", "turn_id"]},
                SESSION_QUERY_METHODS["command"]: {"fields": ["item"]},
                SESSION_QUERY_METHODS["shell"]: {"fields": ["item"]},
            }
        },
    }


def build_interrupt_callback_extension_params(
    *,
    deployment_context: dict[str, str | bool],
) -> dict[str, Any]:
    return {
        "methods": dict(INTERRUPT_CALLBACK_METHODS),
        "shared_workspace_across_consumers": True,
        "tenant_isolation": "none",
        "deployment_context": deployment_context,
    }
