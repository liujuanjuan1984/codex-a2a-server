from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI

from .extension_contracts import (
    INTERRUPT_CALLBACK_METHODS,
    SESSION_CONTROL_METHODS,
    SESSION_QUERY_METHODS,
    build_compatibility_profile_params,
    build_interrupt_callback_extension_params,
    build_session_binding_extension_params,
    build_session_query_extension_params,
    build_streaming_extension_params,
    build_wire_contract_extension_params,
)


def _build_jsonrpc_extension_openapi_description(*, session_shell_enabled: bool) -> str:
    session_methods: list[str] = [
        SESSION_QUERY_METHODS["list_sessions"],
        SESSION_QUERY_METHODS["get_session_messages"],
        SESSION_CONTROL_METHODS["prompt_async"],
        SESSION_CONTROL_METHODS["command"],
    ]
    if session_shell_enabled:
        session_methods.append(SESSION_CONTROL_METHODS["shell"])
    interrupt_methods = ", ".join(sorted(INTERRUPT_CALLBACK_METHODS.values()))
    return (
        "A2A JSON-RPC entrypoint. Supports core A2A methods "
        "(message/send, message/stream, tasks/get, tasks/cancel, tasks/resubscribe) "
        "plus Codex session extensions and shared interrupt callback methods.\n\n"
        f"Codex session query/control methods: {', '.join(session_methods)}.\n"
        f"Shared interrupt callback methods: {interrupt_methods}.\n\n"
        "Notification semantics: extension requests without JSON-RPC id return HTTP 204. "
        "Unsupported methods return JSON-RPC -32601 with supported_methods and "
        "protocol_version in error.data."
    )


def _build_jsonrpc_extension_openapi_examples(*, session_shell_enabled: bool) -> dict[str, Any]:
    examples: dict[str, Any] = {
        "message_send": {
            "summary": "Send message via JSON-RPC core method",
            "value": {
                "jsonrpc": "2.0",
                "id": 101,
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": "msg-1",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Explain what this repository does."}],
                    }
                },
            },
        },
        "message_stream": {
            "summary": "Stream message via JSON-RPC core method",
            "value": {
                "jsonrpc": "2.0",
                "id": 102,
                "method": "message/stream",
                "params": {
                    "message": {
                        "messageId": "msg-stream-1",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Stream the answer and summarize."}],
                    }
                },
            },
        },
        "session_list": {
            "summary": "List Codex sessions",
            "value": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": SESSION_QUERY_METHODS["list_sessions"],
                "params": {"limit": 20},
            },
        },
        "session_messages": {
            "summary": "List messages for a session",
            "value": {
                "jsonrpc": "2.0",
                "id": 2,
                "method": SESSION_QUERY_METHODS["get_session_messages"],
                "params": {"session_id": "s-1", "limit": 20},
            },
        },
        "session_prompt_async": {
            "summary": "Send async prompt to an existing session",
            "value": {
                "jsonrpc": "2.0",
                "id": 21,
                "method": SESSION_CONTROL_METHODS["prompt_async"],
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "parts": [{"type": "text", "text": "Continue and summarize next steps."}]
                    },
                },
            },
        },
        "session_command": {
            "summary": "Send command to an existing session",
            "value": {
                "jsonrpc": "2.0",
                "id": 22,
                "method": SESSION_CONTROL_METHODS["command"],
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "command": "plan",
                        "arguments": "show current work",
                    },
                },
            },
        },
        "permission_reply": {
            "summary": "Reply to permission interrupt request",
            "value": {
                "jsonrpc": "2.0",
                "id": 31,
                "method": INTERRUPT_CALLBACK_METHODS["reply_permission"],
                "params": {"request_id": "req-1", "reply": "once"},
            },
        },
        "question_reply": {
            "summary": "Reply to question interrupt request",
            "value": {
                "jsonrpc": "2.0",
                "id": 32,
                "method": INTERRUPT_CALLBACK_METHODS["reply_question"],
                "params": {"request_id": "req-2", "answers": [["answer"]]},
            },
        },
        "question_reject": {
            "summary": "Reject question interrupt request",
            "value": {
                "jsonrpc": "2.0",
                "id": 33,
                "method": INTERRUPT_CALLBACK_METHODS["reject_question"],
                "params": {"request_id": "req-3"},
            },
        },
    }
    if session_shell_enabled:
        examples["session_shell"] = {
            "summary": "Run shell command attributed to an existing session",
            "value": {
                "jsonrpc": "2.0",
                "id": 23,
                "method": SESSION_CONTROL_METHODS["shell"],
                "params": {
                    "session_id": "s-1",
                    "request": {"command": "git status --short"},
                },
            },
        }
    return examples


def _build_rest_message_openapi_examples() -> dict[str, Any]:
    return {
        "basic_message": {
            "summary": "Send a basic user message (HTTP+JSON)",
            "value": {
                "message": {
                    "messageId": "msg-rest-1",
                    "role": "ROLE_USER",
                    "content": [{"text": "Explain what this repository does."}],
                }
            },
        },
        "continue_session": {
            "summary": "Continue a historical Codex session",
            "value": {
                "message": {
                    "messageId": "msg-rest-continue-1",
                    "role": "ROLE_USER",
                    "content": [{"text": "Continue previous work and summarize next steps."}],
                },
                "metadata": {
                    "shared": {
                        "session": {"id": "s-1"},
                    }
                },
            },
        },
    }


def patch_openapi_contract(
    app: FastAPI,
    *,
    deployment_context: dict[str, str | bool | int],
    directory_override_enabled: bool,
    protocol_version: str,
    session_shell_enabled: bool,
) -> None:
    session_binding = build_session_binding_extension_params(
        deployment_context=deployment_context,
        directory_override_enabled=directory_override_enabled,
    )
    streaming = build_streaming_extension_params()
    session_query = build_session_query_extension_params(
        deployment_context=deployment_context,
        session_shell_enabled=session_shell_enabled,
    )
    interrupt_callback = build_interrupt_callback_extension_params(
        deployment_context=deployment_context,
    )
    wire_contract = build_wire_contract_extension_params(
        protocol_version=protocol_version,
        session_shell_enabled=session_shell_enabled,
    )
    compatibility_profile = build_compatibility_profile_params(
        protocol_version=protocol_version,
        session_shell_enabled=session_shell_enabled,
    )
    original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = original_openapi()
        paths = schema.get("paths")
        if isinstance(paths, dict):
            root_path = paths.get("/")
            if isinstance(root_path, dict):
                post = root_path.get("post")
                if isinstance(post, dict):
                    post["summary"] = "Handle A2A JSON-RPC Requests"
                    post["description"] = _build_jsonrpc_extension_openapi_description(
                        session_shell_enabled=session_shell_enabled
                    )
                    post["x-a2a-extension-contracts"] = {
                        "session_binding": session_binding,
                        "streaming": streaming,
                        "session_query": session_query,
                        "interrupt_callback": interrupt_callback,
                        "wire_contract": wire_contract,
                        "compatibility_profile": compatibility_profile,
                    }

                    request_body = post.setdefault("requestBody", {})
                    if isinstance(request_body, dict):
                        content = request_body.setdefault("content", {})
                        if isinstance(content, dict):
                            app_json = content.setdefault("application/json", {})
                            if isinstance(app_json, dict):
                                app_json["examples"] = _build_jsonrpc_extension_openapi_examples(
                                    session_shell_enabled=session_shell_enabled
                                )

            rest_post_contracts: dict[str, dict[str, Any]] = {
                "/v1/message:send": {
                    "summary": "Send Message (HTTP+JSON)",
                    "description": (
                        "A2A HTTP+JSON message send endpoint. "
                        "Use REST payload shape with message.content and ROLE_* roles."
                    ),
                    "schema_ref": "#/components/schemas/SendMessageRequest",
                    "contracts": {"session_binding": session_binding},
                },
                "/v1/message:stream": {
                    "summary": "Stream Message (HTTP+JSON)",
                    "description": (
                        "A2A HTTP+JSON streaming endpoint. "
                        "Use REST payload shape with message.content and ROLE_* roles."
                    ),
                    "schema_ref": "#/components/schemas/SendStreamingMessageRequest",
                    "contracts": {
                        "session_binding": session_binding,
                        "streaming": streaming,
                        "interrupt_callback": interrupt_callback,
                    },
                },
            }
            rest_examples = _build_rest_message_openapi_examples()
            for rest_path, contract in rest_post_contracts.items():
                rest_path_item = paths.get(rest_path)
                if not isinstance(rest_path_item, dict):
                    continue
                rest_post = rest_path_item.get("post")
                if not isinstance(rest_post, dict):
                    continue

                rest_post["summary"] = contract["summary"]
                rest_post["description"] = contract["description"]
                rest_post["x-a2a-extension-contracts"] = contract["contracts"]
                if rest_path == "/v1/message:stream":
                    rest_post["x-a2a-streaming"] = streaming

                request_body = rest_post.setdefault("requestBody", {})
                if not isinstance(request_body, dict):
                    continue
                request_body.setdefault("required", True)
                content = request_body.setdefault("content", {})
                if not isinstance(content, dict):
                    continue
                app_json = content.setdefault("application/json", {})
                if not isinstance(app_json, dict):
                    continue
                app_json["schema"] = {"$ref": contract["schema_ref"]}
                app_json["examples"] = rest_examples

        app.openapi_schema = schema
        return schema

    cast(Any, app).openapi = custom_openapi


__all__ = ["patch_openapi_contract"]
