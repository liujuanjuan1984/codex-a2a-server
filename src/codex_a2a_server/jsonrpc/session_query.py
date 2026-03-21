from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from a2a.types import A2AError, InternalError, JSONRPCError, JSONRPCRequest
from starlette.responses import Response

from codex_a2a_server.jsonrpc.errors import (
    ERR_SESSION_NOT_FOUND,
    ERR_UPSTREAM_HTTP_ERROR,
    ERR_UPSTREAM_PAYLOAD_ERROR,
    ERR_UPSTREAM_UNREACHABLE,
    invalid_params_response,
)
from codex_a2a_server.jsonrpc.params import (
    JsonRpcParamsValidationError,
    parse_get_session_messages_params,
    parse_list_sessions_params,
)
from codex_a2a_server.jsonrpc.payload_mapping import (
    as_a2a_message,
    as_a2a_session_task,
    extract_raw_items,
)

if TYPE_CHECKING:
    from codex_a2a_server.jsonrpc.application import CodexSessionQueryJSONRPCApplication

logger = logging.getLogger(__name__)


async def handle_session_query_request(
    app: CodexSessionQueryJSONRPCApplication,
    base_request: JSONRPCRequest,
    params: dict[str, Any],
) -> Response:
    try:
        if base_request.method == app._method_list_sessions:
            query = parse_list_sessions_params(params)
            session_id: str | None = None
        else:
            session_id, query = parse_get_session_messages_params(params)
    except JsonRpcParamsValidationError as exc:
        return invalid_params_response(app, base_request.id, exc)

    try:
        if session_id is None:
            raw_result = await app._codex_client.list_sessions(params=query)
        else:
            raw_result = await app._codex_client.list_messages(session_id, params=query)
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        if upstream_status == 404 and base_request.method == app._method_get_session_messages:
            return app._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_SESSION_NOT_FOUND,
                    message="Session not found",
                    data={"type": "SESSION_NOT_FOUND", "session_id": session_id},
                ),
            )
        return app._generate_error_response(
            base_request.id,
            JSONRPCError(
                code=ERR_UPSTREAM_HTTP_ERROR,
                message="Upstream Codex error",
                data={
                    "type": "UPSTREAM_HTTP_ERROR",
                    "upstream_status": upstream_status,
                },
            ),
        )
    except httpx.HTTPError:
        return app._generate_error_response(
            base_request.id,
            JSONRPCError(
                code=ERR_UPSTREAM_UNREACHABLE,
                message="Upstream Codex unreachable",
                data={"type": "UPSTREAM_UNREACHABLE"},
            ),
        )
    except Exception as exc:
        logger.exception("Codex session query JSON-RPC method failed")
        return app._generate_error_response(
            base_request.id,
            A2AError(root=InternalError(message=str(exc))),
        )

    try:
        if base_request.method == app._method_list_sessions:
            raw_items = extract_raw_items(raw_result, kind="sessions")
        else:
            raw_items = extract_raw_items(raw_result, kind="messages")
    except ValueError as exc:
        logger.warning("Upstream Codex payload mismatch: %s", exc)
        return app._generate_error_response(
            base_request.id,
            JSONRPCError(
                code=ERR_UPSTREAM_PAYLOAD_ERROR,
                message="Upstream Codex payload mismatch",
                data={"type": "UPSTREAM_PAYLOAD_ERROR", "detail": str(exc)},
            ),
        )

    if base_request.method == app._method_list_sessions:
        items = [task for item in raw_items if (task := as_a2a_session_task(item)) is not None]
    else:
        assert session_id is not None
        items = [
            message
            for item in raw_items
            if (message := as_a2a_message(session_id, item)) is not None
        ]

    if base_request.id is None:
        return Response(status_code=204)
    return app._jsonrpc_success_response(base_request.id, {"items": items})
