from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import Response

from codex_a2a_server.jsonrpc.errors import (
    ERR_INTERRUPT_EXPIRED,
    ERR_INTERRUPT_NOT_FOUND,
    ERR_INTERRUPT_TYPE_MISMATCH,
    interrupt_error_response,
)
from codex_a2a_server.upstream.interrupts import InterruptRequestBinding, InterruptRequestError

if TYPE_CHECKING:
    from codex_a2a_server.jsonrpc.application import CodexSessionQueryJSONRPCApplication


def resolve_interrupt_binding(
    app: CodexSessionQueryJSONRPCApplication,
    *,
    request_id: str,
    response_id: str | int | None,
    expected_interrupt_type: str,
) -> tuple[InterruptRequestBinding | None, Response | None]:
    interrupt_status, binding = app._codex_client.resolve_interrupt_request(request_id)
    if interrupt_status == "missing":
        return None, interrupt_error_response(
            app,
            response_id,
            code=ERR_INTERRUPT_NOT_FOUND,
            message="Interrupt request not found",
            data={"type": "INTERRUPT_REQUEST_NOT_FOUND", "request_id": request_id},
        )
    if interrupt_status == "expired":
        return None, interrupt_error_response(
            app,
            response_id,
            code=ERR_INTERRUPT_EXPIRED,
            message="Interrupt request expired",
            data={"type": "INTERRUPT_REQUEST_EXPIRED", "request_id": request_id},
        )
    if binding is not None and binding.interrupt_type != expected_interrupt_type:
        return None, interrupt_error_response(
            app,
            response_id,
            code=ERR_INTERRUPT_TYPE_MISMATCH,
            message="Interrupt callback type mismatch",
            data={
                "type": "INTERRUPT_TYPE_MISMATCH",
                "request_id": request_id,
                "expected_interrupt_type": expected_interrupt_type,
                "actual_interrupt_type": binding.interrupt_type,
            },
        )
    return binding, None


async def validate_interrupt_owner(
    app: CodexSessionQueryJSONRPCApplication,
    *,
    request: Request,
    binding: InterruptRequestBinding | None,
    request_id: str,
    response_id: str | int | None,
) -> Response | None:
    identity = getattr(request.state, "user_identity", None)
    if not isinstance(identity, str) or not identity.strip():
        return None
    if binding is None or not binding.session_id or app._session_owner_matcher is None:
        return None
    matches = await app._session_owner_matcher(
        identity=identity.strip(),
        session_id=binding.session_id,
    )
    if matches is False:
        return interrupt_error_response(
            app,
            response_id,
            code=ERR_INTERRUPT_NOT_FOUND,
            message="Interrupt request not found",
            data={"type": "INTERRUPT_REQUEST_NOT_FOUND", "request_id": request_id},
        )
    return None


def interrupt_error_from_exception(
    app: CodexSessionQueryJSONRPCApplication,
    request_id: str | int | None,
    exc: InterruptRequestError,
) -> Response:
    if exc.error_type == "INTERRUPT_REQUEST_EXPIRED":
        return interrupt_error_response(
            app,
            request_id,
            code=ERR_INTERRUPT_EXPIRED,
            message="Interrupt request expired",
            data={"type": exc.error_type, "request_id": exc.request_id},
        )
    if exc.error_type == "INTERRUPT_TYPE_MISMATCH":
        return interrupt_error_response(
            app,
            request_id,
            code=ERR_INTERRUPT_TYPE_MISMATCH,
            message="Interrupt callback type mismatch",
            data={
                "type": exc.error_type,
                "request_id": exc.request_id,
                "expected_interrupt_type": exc.expected_interrupt_type,
                "actual_interrupt_type": exc.actual_interrupt_type,
            },
        )
    return interrupt_error_response(
        app,
        request_id,
        code=ERR_INTERRUPT_NOT_FOUND,
        message="Interrupt request not found",
        data={"type": "INTERRUPT_REQUEST_NOT_FOUND", "request_id": exc.request_id},
    )
