from __future__ import annotations

from typing import TYPE_CHECKING, Any

from a2a.types import A2AError, InvalidParamsError, JSONRPCError
from starlette.responses import Response

from codex_a2a_server.jsonrpc.params import JsonRpcParamsValidationError

if TYPE_CHECKING:
    from codex_a2a_server.jsonrpc.application import CodexSessionQueryJSONRPCApplication

ERR_SESSION_NOT_FOUND = -32001
ERR_SESSION_FORBIDDEN = -32006
ERR_UPSTREAM_UNREACHABLE = -32002
ERR_UPSTREAM_HTTP_ERROR = -32003
ERR_INTERRUPT_NOT_FOUND = -32004
ERR_UPSTREAM_PAYLOAD_ERROR = -32005
ERR_INTERRUPT_EXPIRED = -32007
ERR_INTERRUPT_TYPE_MISMATCH = -32008


def interrupt_expected_type(method: str, *, permission_method: str) -> str:
    if method == permission_method:
        return "permission"
    return "question"


def invalid_params_response(
    app: CodexSessionQueryJSONRPCApplication,
    request_id: str | int | None,
    exc: JsonRpcParamsValidationError,
) -> Response:
    return app._generate_error_response(
        request_id,
        A2AError(root=InvalidParamsError(message=str(exc), data=exc.data)),
    )


def session_forbidden_response(
    app: CodexSessionQueryJSONRPCApplication,
    request_id: str | int | None,
    *,
    session_id: str,
) -> Response:
    return app._generate_error_response(
        request_id,
        JSONRPCError(
            code=ERR_SESSION_FORBIDDEN,
            message="Session forbidden",
            data={"type": "SESSION_FORBIDDEN", "session_id": session_id},
        ),
    )


def extract_directory_from_metadata(
    app: CodexSessionQueryJSONRPCApplication,
    *,
    request_id: str | int | None,
    directory: str | None,
) -> tuple[str | None, Response | None]:
    if directory is None:
        return None, None
    if app._directory_resolver is None:
        return directory, None
    try:
        return app._directory_resolver(directory), None
    except ValueError as exc:
        return None, app._generate_error_response(
            request_id,
            A2AError(
                root=InvalidParamsError(
                    message=str(exc),
                    data={"type": "INVALID_FIELD", "field": "metadata.codex.directory"},
                )
            ),
        )


def interrupt_error_response(
    app: CodexSessionQueryJSONRPCApplication,
    request_id: str | int | None,
    *,
    code: int,
    message: str,
    data: dict[str, Any],
) -> Response:
    return app._generate_error_response(
        request_id,
        JSONRPCError(
            code=code,
            message=message,
            data=data,
        ),
    )
