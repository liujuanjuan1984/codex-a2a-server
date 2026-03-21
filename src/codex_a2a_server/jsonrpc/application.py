from __future__ import annotations

from typing import Any

from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.types import (
    A2AError,
    InvalidParamsError,
    InvalidRequestError,
    JSONRPCError,
    JSONRPCRequest,
)
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from codex_a2a_server.jsonrpc.interrupts import handle_interrupt_callback_request
from codex_a2a_server.jsonrpc.session_control import handle_session_control_request
from codex_a2a_server.jsonrpc.session_query import handle_session_query_request
from codex_a2a_server.upstream.client import CodexClient


class CodexSessionQueryJSONRPCApplication(A2AFastAPIApplication):
    """Extend A2A JSON-RPC endpoint with Codex session query methods.

    These methods are optional (declared via AgentCard.capabilities.extensions) and do
    not require additional private REST endpoints.
    """

    def __init__(
        self,
        *args: Any,
        codex_client: CodexClient,
        methods: dict[str, str],
        protocol_version: str,
        supported_methods: list[str],
        directory_resolver=None,
        session_claim=None,
        session_claim_finalize=None,
        session_claim_release=None,
        session_owner_matcher=None,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._codex_client = codex_client
        self._method_list_sessions = methods["list_sessions"]
        self._method_get_session_messages = methods["get_session_messages"]
        self._method_prompt_async = methods["prompt_async"]
        self._method_command = methods["command"]
        self._method_shell = methods.get("shell")
        self._method_reply_permission = methods["reply_permission"]
        self._method_reply_question = methods["reply_question"]
        self._method_reject_question = methods["reject_question"]
        self._protocol_version = protocol_version
        self._supported_methods = list(supported_methods)
        self._supported_method_set = set(supported_methods)
        self._extension_method_set = {
            self._method_list_sessions,
            self._method_get_session_messages,
            self._method_prompt_async,
            self._method_command,
            self._method_reply_permission,
            self._method_reply_question,
            self._method_reject_question,
        }
        if self._method_shell is not None:
            self._extension_method_set.add(self._method_shell)
        self._directory_resolver = directory_resolver
        self._session_claim = session_claim
        self._session_claim_finalize = session_claim_finalize
        self._session_claim_release = session_claim_release
        self._session_owner_matcher = session_owner_matcher
        self._validate_guard_hooks()

    def _validate_guard_hooks(self) -> None:
        missing_for_session_control: list[str] = []
        if self._session_claim is None:
            missing_for_session_control.append("session_claim")
        if self._session_claim_finalize is None:
            missing_for_session_control.append("session_claim_finalize")
        if self._session_claim_release is None:
            missing_for_session_control.append("session_claim_release")
        if missing_for_session_control:
            missing = ", ".join(missing_for_session_control)
            raise ValueError(
                "CodexSessionQueryJSONRPCApplication missing required session control hooks: "
                f"{missing}"
            )

        if self._session_owner_matcher is None:
            raise ValueError(
                "CodexSessionQueryJSONRPCApplication missing required interrupt ownership "
                "hook: session_owner_matcher"
            )

    async def _handle_requests(self, request: Request) -> Response:
        request_id: str | int | None = None
        try:
            body = await request.json()
            if isinstance(body, dict):
                request_id = body.get("id")
                if request_id is not None and not isinstance(request_id, str | int):
                    request_id = None

            if not self._allowed_content_length(request):
                return self._generate_error_response(
                    request_id,
                    A2AError(root=InvalidRequestError(message="Payload too large")),
                )

            base_request = JSONRPCRequest.model_validate(body)
        except Exception:
            return await super()._handle_requests(request)

        if base_request.method not in self._supported_method_set:
            if base_request.id is None:
                return Response(status_code=204)
            return self._unsupported_method_response(base_request.id, base_request.method)

        if base_request.method not in self._extension_method_set:
            return await super()._handle_requests(request)

        params = base_request.params or {}
        if not isinstance(params, dict):
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InvalidParamsError(message="params must be an object")),
            )

        session_query_methods = {
            self._method_list_sessions,
            self._method_get_session_messages,
        }
        session_control_methods = {
            self._method_prompt_async,
            self._method_command,
        }
        if self._method_shell is not None:
            session_control_methods.add(self._method_shell)

        if base_request.method in session_query_methods:
            return await handle_session_query_request(self, base_request, params)
        if base_request.method in session_control_methods:
            return await handle_session_control_request(
                self,
                base_request,
                params,
                request=request,
            )
        return await handle_interrupt_callback_request(
            self,
            base_request,
            params,
            request=request,
        )

    def _unsupported_method_response(
        self,
        request_id: str | int,
        method: str,
    ) -> JSONResponse:
        return self._generate_error_response(
            request_id,
            JSONRPCError(
                code=-32601,
                message=f"Unsupported method: {method}",
                data={
                    "type": "METHOD_NOT_SUPPORTED",
                    "method": method,
                    "supported_methods": self._supported_methods,
                    "protocol_version": self._protocol_version,
                },
            ),
        )

    def _jsonrpc_success_response(self, request_id: str | int, result: Any) -> JSONResponse:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        )
