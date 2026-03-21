from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

import httpx
from a2a.types import A2AError, InternalError, JSONRPCError, JSONRPCRequest
from starlette.requests import Request
from starlette.responses import Response

from codex_a2a_server.jsonrpc.errors import (
    ERR_INTERRUPT_NOT_FOUND,
    ERR_UPSTREAM_HTTP_ERROR,
    ERR_UPSTREAM_UNREACHABLE,
    extract_directory_from_metadata,
    interrupt_error_response,
    interrupt_expected_type,
    invalid_params_response,
)
from codex_a2a_server.jsonrpc.interrupt_lifecycle import (
    interrupt_error_from_exception,
    resolve_interrupt_binding,
    validate_interrupt_owner,
)
from codex_a2a_server.jsonrpc.params import (
    JsonRpcParamsValidationError,
    PermissionReplyParams,
    QuestionRejectParams,
    QuestionReplyParams,
    parse_permission_reply_params,
    parse_question_reject_params,
    parse_question_reply_params,
)
from codex_a2a_server.upstream.interrupts import InterruptRequestError

if TYPE_CHECKING:
    from codex_a2a_server.jsonrpc.application import CodexSessionQueryJSONRPCApplication

logger = logging.getLogger(__name__)


async def handle_interrupt_callback_request(
    app: CodexSessionQueryJSONRPCApplication,
    base_request: JSONRPCRequest,
    params: dict[str, object],
    *,
    request: Request,
) -> Response:
    parsed_params: PermissionReplyParams | QuestionReplyParams | QuestionRejectParams
    try:
        if base_request.method == app._method_reply_permission:
            parsed_params = parse_permission_reply_params(params)
        elif base_request.method == app._method_reply_question:
            parsed_params = parse_question_reply_params(params)
        else:
            parsed_params = parse_question_reject_params(params)
    except JsonRpcParamsValidationError as exc:
        return invalid_params_response(app, base_request.id, exc)

    request_id = parsed_params.request_id
    directory, metadata_error = extract_directory_from_metadata(
        app,
        request_id=base_request.id,
        directory=(
            parsed_params.metadata.codex.directory
            if parsed_params.metadata is not None and parsed_params.metadata.codex is not None
            else None
        ),
    )
    if metadata_error is not None:
        return metadata_error

    expected_interrupt_type = interrupt_expected_type(
        base_request.method,
        permission_method=app._method_reply_permission,
    )
    binding, binding_error = resolve_interrupt_binding(
        app,
        request_id=request_id,
        response_id=base_request.id,
        expected_interrupt_type=expected_interrupt_type,
    )
    if binding_error is not None:
        return binding_error

    owner_error = await validate_interrupt_owner(
        app,
        request=request,
        binding=binding,
        request_id=request_id,
        response_id=base_request.id,
    )
    if owner_error is not None:
        return owner_error

    try:
        if base_request.method == app._method_reply_permission:
            permission_params = cast(PermissionReplyParams, parsed_params)
            reply = permission_params.reply
            message = permission_params.message
            await app._codex_client.permission_reply(
                request_id,
                reply=reply,
                message=message,
                directory=directory,
            )
            result: dict[str, object] = {"ok": True, "request_id": request_id, "reply": reply}
        elif base_request.method == app._method_reply_question:
            question_params = cast(QuestionReplyParams, parsed_params)
            answers = question_params.answers
            await app._codex_client.question_reply(
                request_id,
                answers=answers,
                directory=directory,
            )
            result = {"ok": True, "request_id": request_id, "answers": answers}
        else:
            await app._codex_client.question_reject(request_id, directory=directory)
            result = {"ok": True, "request_id": request_id}
        app._codex_client.discard_interrupt_request(request_id)
    except InterruptRequestError as exc:
        return interrupt_error_from_exception(app, base_request.id, exc)
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        if upstream_status == 404:
            app._codex_client.discard_interrupt_request(request_id)
            return interrupt_error_response(
                app,
                base_request.id,
                code=ERR_INTERRUPT_NOT_FOUND,
                message="Interrupt request not found",
                data={"type": "INTERRUPT_REQUEST_NOT_FOUND", "request_id": request_id},
            )
        return app._generate_error_response(
            base_request.id,
            JSONRPCError(
                code=ERR_UPSTREAM_HTTP_ERROR,
                message="Upstream Codex error",
                data={
                    "type": "UPSTREAM_HTTP_ERROR",
                    "upstream_status": upstream_status,
                    "request_id": request_id,
                },
            ),
        )
    except httpx.HTTPError:
        return app._generate_error_response(
            base_request.id,
            JSONRPCError(
                code=ERR_UPSTREAM_UNREACHABLE,
                message="Upstream Codex unreachable",
                data={"type": "UPSTREAM_UNREACHABLE", "request_id": request_id},
            ),
        )
    except Exception as exc:
        logger.exception("Codex interrupt callback JSON-RPC method failed")
        return app._generate_error_response(
            base_request.id,
            A2AError(root=InternalError(message=str(exc))),
        )

    if base_request.id is None:
        return Response(status_code=204)
    return app._jsonrpc_success_response(base_request.id, result)
