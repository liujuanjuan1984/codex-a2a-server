from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from a2a.types import A2AError, InternalError, JSONRPCError, JSONRPCRequest
from starlette.requests import Request
from starlette.responses import Response

from codex_a2a_server.jsonrpc.errors import (
    ERR_SESSION_NOT_FOUND,
    ERR_UPSTREAM_HTTP_ERROR,
    ERR_UPSTREAM_UNREACHABLE,
    extract_directory_from_metadata,
    invalid_params_response,
    session_forbidden_response,
)
from codex_a2a_server.jsonrpc.params import (
    CommandControlParams,
    JsonRpcParamsValidationError,
    PromptAsyncControlParams,
    ShellControlParams,
    parse_command_params,
    parse_prompt_async_params,
    parse_shell_params,
)
from codex_a2a_server.jsonrpc.payload_mapping import (
    as_a2a_message,
    message_to_item,
)

if TYPE_CHECKING:
    from codex_a2a_server.jsonrpc.application import CodexSessionQueryJSONRPCApplication

logger = logging.getLogger(__name__)


async def handle_session_control_request(
    app: CodexSessionQueryJSONRPCApplication,
    base_request: JSONRPCRequest,
    params: dict[str, Any],
    *,
    request: Request,
) -> Response:
    parsed_params: PromptAsyncControlParams | CommandControlParams | ShellControlParams
    try:
        if base_request.method == app._method_prompt_async:
            parsed_params = parse_prompt_async_params(params)
        elif base_request.method == app._method_command:
            parsed_params = parse_command_params(params)
        else:
            assert app._method_shell is not None
            parsed_params = parse_shell_params(params)
    except JsonRpcParamsValidationError as exc:
        return invalid_params_response(app, base_request.id, exc)

    session_id = parsed_params.session_id
    request_payload = parsed_params.request.model_dump(by_alias=True, exclude_none=True)
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

    identity = getattr(request.state, "user_identity", None)
    pending_claim = False
    claim_finalized = False
    if isinstance(identity, str) and identity and app._session_claim is not None:
        try:
            pending_claim = await app._session_claim(identity=identity, session_id=session_id)
        except PermissionError:
            return session_forbidden_response(app, base_request.id, session_id=session_id)

    try:
        if base_request.method == app._method_prompt_async:
            result: dict[str, Any] = await app._codex_client.session_prompt_async(
                session_id,
                request=request_payload,
                directory=directory,
            )
        elif base_request.method == app._method_command:
            command_result = await app._codex_client.session_command(
                session_id,
                request=request_payload,
                directory=directory,
            )
            item = as_a2a_message(session_id, message_to_item(command_result))
            if item is None:
                raise RuntimeError(
                    "Codex session command response could not be mapped to A2A Message"
                )
            result = {"item": item}
        else:
            shell_result = await app._codex_client.session_shell(
                session_id,
                request=request_payload,
                directory=directory,
            )
            item = as_a2a_message(session_id, shell_result)
            if item is None:
                raise RuntimeError(
                    "Codex session shell response could not be mapped to A2A Message"
                )
            result = {"item": item}

        if pending_claim and isinstance(identity, str) and app._session_claim_finalize is not None:
            await app._session_claim_finalize(identity=identity, session_id=session_id)
            claim_finalized = True
    except PermissionError:
        return session_forbidden_response(app, base_request.id, session_id=session_id)
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        if upstream_status == 404:
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
                    "session_id": session_id,
                },
            ),
        )
    except httpx.HTTPError:
        return app._generate_error_response(
            base_request.id,
            JSONRPCError(
                code=ERR_UPSTREAM_UNREACHABLE,
                message="Upstream Codex unreachable",
                data={"type": "UPSTREAM_UNREACHABLE", "session_id": session_id},
            ),
        )
    except Exception as exc:
        logger.exception("Codex session control JSON-RPC method failed")
        return app._generate_error_response(
            base_request.id,
            A2AError(root=InternalError(message=str(exc))),
        )
    finally:
        if (
            pending_claim
            and not claim_finalized
            and isinstance(identity, str)
            and app._session_claim_release is not None
        ):
            await app._session_claim_release(identity=identity, session_id=session_id)

    if base_request.id is None:
        return Response(status_code=204)
    return app._jsonrpc_success_response(base_request.id, result)
