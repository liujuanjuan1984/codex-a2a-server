from __future__ import annotations

import logging
from typing import Any, cast

import httpx
from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.types import (
    A2AError,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONRPCError,
    JSONRPCRequest,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from .codex_client import CodexClient, InterruptRequestBinding, InterruptRequestError
from .jsonrpc_models import (
    CommandControlParams,
    JsonRpcParamsValidationError,
    PermissionReplyParams,
    PromptAsyncControlParams,
    QuestionRejectParams,
    QuestionReplyParams,
    ShellControlParams,
    parse_command_params,
    parse_get_session_messages_params,
    parse_list_sessions_params,
    parse_permission_reply_params,
    parse_prompt_async_params,
    parse_question_reject_params,
    parse_question_reply_params,
    parse_shell_params,
)
from .text_parts import extract_text_from_parts

logger = logging.getLogger(__name__)

ERR_SESSION_NOT_FOUND = -32001
ERR_SESSION_FORBIDDEN = -32006
ERR_UPSTREAM_UNREACHABLE = -32002
ERR_UPSTREAM_HTTP_ERROR = -32003
ERR_INTERRUPT_NOT_FOUND = -32004
ERR_UPSTREAM_PAYLOAD_ERROR = -32005
ERR_INTERRUPT_EXPIRED = -32007
ERR_INTERRUPT_TYPE_MISMATCH = -32008


def _session_context_id(session_id: str) -> str:
    return session_id


def _interrupt_expected_type(method: str, *, permission_method: str) -> str:
    if method == permission_method:
        return "permission"
    return "question"


def _extract_session_title(session: dict[str, Any]) -> str:
    title = session.get("title")
    if not isinstance(title, str):
        return ""
    return title.strip()


def _as_a2a_session_task(session: Any) -> dict[str, Any] | None:
    if not isinstance(session, dict):
        return None
    raw_id = session.get("id")
    if not isinstance(raw_id, str):
        return None
    session_id = raw_id.strip()
    if not session_id:
        return None
    title = _extract_session_title(session)
    if not title:
        return None
    task = Task(
        id=session_id,
        context_id=_session_context_id(session_id),
        # Model Codex sessions as completed A2A Tasks for stable downstream rendering.
        status=TaskStatus(state=TaskState.completed),
        metadata={
            "shared": {"session": {"id": session_id, "title": title}},
            "codex": {"raw": session},
        },
    )
    return task.model_dump(by_alias=True, exclude_none=True)


def _as_a2a_message(session_id: str, item: Any) -> dict[str, Any] | None:
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

    msg = Message(
        message_id=message_id,
        role=role,
        parts=[Part(root=TextPart(text=text))],
        context_id=_session_context_id(session_id),
        metadata={
            "shared": {"session": {"id": session_id}},
            "codex": {"raw": item},
        },
    )
    return msg.model_dump(by_alias=True, exclude_none=True)


def _message_to_item(message: Any) -> dict[str, Any]:
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


def _extract_raw_items(raw_result: Any, *, kind: str) -> list[Any]:
    """Extract list payloads from Codex responses."""
    if isinstance(raw_result, list):
        return raw_result
    raise ValueError(f"Codex {kind} payload must be an array; got {type(raw_result).__name__}")


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
        # Fast path: sniff method first then either handle here or delegate.
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
            # Delegate to base implementation for consistent error handling.
            return await super()._handle_requests(request)

        if base_request.method not in self._supported_method_set:
            if base_request.id is None:
                return Response(status_code=204)
            return self._unsupported_method_response(base_request.id, base_request.method)

        if base_request.method not in self._extension_method_set:
            return await super()._handle_requests(request)

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
        params = base_request.params or {}
        if not isinstance(params, dict):
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InvalidParamsError(message="params must be an object")),
            )

        if base_request.method in session_query_methods:
            return await self._handle_session_query_request(base_request, params)
        if base_request.method in session_control_methods:
            return await self._handle_session_control_request(base_request, params, request=request)
        return await self._handle_interrupt_callback_request(base_request, params, request=request)

    async def _handle_session_query_request(
        self,
        base_request: JSONRPCRequest,
        params: dict[str, Any],
    ) -> Response:
        try:
            if base_request.method == self._method_list_sessions:
                query = parse_list_sessions_params(params)
                session_id: str | None = None
            else:
                session_id, query = parse_get_session_messages_params(params)
        except JsonRpcParamsValidationError as exc:
            return self._invalid_params_response(base_request.id, exc)

        try:
            if session_id is None:
                raw_result = await self._codex_client.list_sessions(params=query)
            else:
                raw_result = await self._codex_client.list_messages(session_id, params=query)
        except httpx.HTTPStatusError as exc:
            upstream_status = exc.response.status_code
            if upstream_status == 404 and base_request.method == self._method_get_session_messages:
                return self._generate_error_response(
                    base_request.id,
                    JSONRPCError(
                        code=ERR_SESSION_NOT_FOUND,
                        message="Session not found",
                        data={"type": "SESSION_NOT_FOUND", "session_id": session_id},
                    ),
                )
            return self._generate_error_response(
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
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_UNREACHABLE,
                    message="Upstream Codex unreachable",
                    data={"type": "UPSTREAM_UNREACHABLE"},
                ),
            )
        except Exception as exc:
            logger.exception("Codex session query JSON-RPC method failed")
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InternalError(message=str(exc))),
            )

        try:
            if base_request.method == self._method_list_sessions:
                raw_items = _extract_raw_items(raw_result, kind="sessions")
            else:
                raw_items = _extract_raw_items(raw_result, kind="messages")
        except ValueError as exc:
            logger.warning("Upstream Codex payload mismatch: %s", exc)
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_PAYLOAD_ERROR,
                    message="Upstream Codex payload mismatch",
                    data={"type": "UPSTREAM_PAYLOAD_ERROR", "detail": str(exc)},
                ),
            )

        # Protocol: items are always arrays of A2A objects.
        # Task for sessions; Message for messages.
        if base_request.method == self._method_list_sessions:
            mapped: list[dict[str, Any]] = []
            for item in raw_items:
                task = _as_a2a_session_task(item)
                if task is not None:
                    mapped.append(task)
            items: list[dict[str, Any]] = mapped
        else:
            assert session_id is not None
            mapped = []
            for item in raw_items:
                message = _as_a2a_message(session_id, item)
                if message is not None:
                    mapped.append(message)
            items = mapped

        result = {
            "items": items,
        }

        # Notifications (id omitted) should not yield a response.
        if base_request.id is None:
            return Response(status_code=204)

        return self._jsonrpc_success_response(
            base_request.id,
            result,
        )

    def _session_forbidden_response(
        self,
        request_id: str | int | None,
        *,
        session_id: str,
    ) -> Response:
        return self._generate_error_response(
            request_id,
            JSONRPCError(
                code=ERR_SESSION_FORBIDDEN,
                message="Session forbidden",
                data={"type": "SESSION_FORBIDDEN", "session_id": session_id},
            ),
        )

    def _extract_directory_from_metadata(
        self,
        *,
        request_id: str | int | None,
        directory: str | None,
    ) -> tuple[str | None, Response | None]:
        if directory is None:
            return None, None
        if self._directory_resolver is None:
            return directory, None
        try:
            return self._directory_resolver(directory), None
        except ValueError as exc:
            return None, self._generate_error_response(
                request_id,
                A2AError(
                    root=InvalidParamsError(
                        message=str(exc),
                        data={"type": "INVALID_FIELD", "field": "metadata.codex.directory"},
                    )
                ),
            )

    def _invalid_params_response(
        self,
        request_id: str | int | None,
        exc: JsonRpcParamsValidationError,
    ) -> JSONResponse:
        return self._generate_error_response(
            request_id,
            A2AError(root=InvalidParamsError(message=str(exc), data=exc.data)),
        )

    async def _handle_session_control_request(
        self,
        base_request: JSONRPCRequest,
        params: dict[str, Any],
        *,
        request: Request,
    ) -> Response:
        parsed_params: PromptAsyncControlParams | CommandControlParams | ShellControlParams
        try:
            if base_request.method == self._method_prompt_async:
                parsed_params = parse_prompt_async_params(params)
            elif base_request.method == self._method_command:
                parsed_params = parse_command_params(params)
            else:
                assert self._method_shell is not None
                parsed_params = parse_shell_params(params)
        except JsonRpcParamsValidationError as exc:
            return self._invalid_params_response(base_request.id, exc)

        session_id = parsed_params.session_id
        request_payload = parsed_params.request.model_dump(by_alias=True, exclude_none=True)
        directory, metadata_error = self._extract_directory_from_metadata(
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
        if isinstance(identity, str) and identity and self._session_claim is not None:
            try:
                pending_claim = await self._session_claim(identity=identity, session_id=session_id)
            except PermissionError:
                return self._session_forbidden_response(base_request.id, session_id=session_id)

        try:
            if base_request.method == self._method_prompt_async:
                result = await self._codex_client.session_prompt_async(
                    session_id,
                    request=request_payload,
                    directory=directory,
                )
            elif base_request.method == self._method_command:
                command_result = await self._codex_client.session_command(
                    session_id,
                    request=request_payload,
                    directory=directory,
                )
                item = _as_a2a_message(session_id, _message_to_item(command_result))
                if item is None:
                    raise RuntimeError(
                        "Codex session command response could not be mapped to A2A Message"
                    )
                result = {"item": item}
            else:
                shell_result = await self._codex_client.session_shell(
                    session_id,
                    request=request_payload,
                    directory=directory,
                )
                item = _as_a2a_message(session_id, shell_result)
                if item is None:
                    raise RuntimeError(
                        "Codex session shell response could not be mapped to A2A Message"
                    )
                result = {"item": item}

            if (
                pending_claim
                and isinstance(identity, str)
                and self._session_claim_finalize is not None
            ):
                await self._session_claim_finalize(identity=identity, session_id=session_id)
                claim_finalized = True
        except PermissionError:
            return self._session_forbidden_response(base_request.id, session_id=session_id)
        except httpx.HTTPStatusError as exc:
            upstream_status = exc.response.status_code
            if upstream_status == 404:
                return self._generate_error_response(
                    base_request.id,
                    JSONRPCError(
                        code=ERR_SESSION_NOT_FOUND,
                        message="Session not found",
                        data={"type": "SESSION_NOT_FOUND", "session_id": session_id},
                    ),
                )
            return self._generate_error_response(
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
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_UNREACHABLE,
                    message="Upstream Codex unreachable",
                    data={"type": "UPSTREAM_UNREACHABLE", "session_id": session_id},
                ),
            )
        except Exception as exc:
            logger.exception("Codex session control JSON-RPC method failed")
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InternalError(message=str(exc))),
            )
        finally:
            if (
                pending_claim
                and not claim_finalized
                and isinstance(identity, str)
                and self._session_claim_release is not None
            ):
                await self._session_claim_release(identity=identity, session_id=session_id)

        if base_request.id is None:
            return Response(status_code=204)

        return self._jsonrpc_success_response(base_request.id, result)

    async def _handle_interrupt_callback_request(
        self,
        base_request: JSONRPCRequest,
        params: dict[str, Any],
        *,
        request: Request,
    ) -> Response:
        parsed_params: PermissionReplyParams | QuestionReplyParams | QuestionRejectParams
        try:
            if base_request.method == self._method_reply_permission:
                parsed_params = parse_permission_reply_params(params)
            elif base_request.method == self._method_reply_question:
                parsed_params = parse_question_reply_params(params)
            else:
                parsed_params = parse_question_reject_params(params)
        except JsonRpcParamsValidationError as exc:
            return self._invalid_params_response(base_request.id, exc)

        request_id = parsed_params.request_id
        directory, metadata_error = self._extract_directory_from_metadata(
            request_id=base_request.id,
            directory=(
                parsed_params.metadata.codex.directory
                if parsed_params.metadata is not None and parsed_params.metadata.codex is not None
                else None
            ),
        )
        if metadata_error is not None:
            return metadata_error

        expected_interrupt_type = _interrupt_expected_type(
            base_request.method,
            permission_method=self._method_reply_permission,
        )
        interrupt_status, binding = self._codex_client.resolve_interrupt_request(request_id)
        if interrupt_status == "missing":
            return self._interrupt_error_response(
                base_request.id,
                code=ERR_INTERRUPT_NOT_FOUND,
                message="Interrupt request not found",
                data={
                    "type": "INTERRUPT_REQUEST_NOT_FOUND",
                    "request_id": request_id,
                },
            )
        if interrupt_status == "expired":
            return self._interrupt_error_response(
                base_request.id,
                code=ERR_INTERRUPT_EXPIRED,
                message="Interrupt request expired",
                data={
                    "type": "INTERRUPT_REQUEST_EXPIRED",
                    "request_id": request_id,
                },
            )
        if binding is not None and binding.interrupt_type != expected_interrupt_type:
            return self._interrupt_error_response(
                base_request.id,
                code=ERR_INTERRUPT_TYPE_MISMATCH,
                message="Interrupt callback type mismatch",
                data={
                    "type": "INTERRUPT_TYPE_MISMATCH",
                    "request_id": request_id,
                    "expected_interrupt_type": expected_interrupt_type,
                    "actual_interrupt_type": binding.interrupt_type,
                },
            )
        owner_error = await self._validate_interrupt_owner(
            request=request,
            binding=binding,
            request_id=request_id,
            response_id=base_request.id,
        )
        if owner_error is not None:
            return owner_error

        try:
            if base_request.method == self._method_reply_permission:
                permission_params = cast(PermissionReplyParams, parsed_params)
                reply = permission_params.reply
                message = permission_params.message
                await self._codex_client.permission_reply(
                    request_id,
                    reply=reply,
                    message=message,
                    directory=directory,
                )
                result: dict[str, Any] = {
                    "ok": True,
                    "request_id": request_id,
                    "reply": reply,
                }
            elif base_request.method == self._method_reply_question:
                question_params = cast(QuestionReplyParams, parsed_params)
                answers = question_params.answers
                await self._codex_client.question_reply(
                    request_id,
                    answers=answers,
                    directory=directory,
                )
                result = {
                    "ok": True,
                    "request_id": request_id,
                    "answers": answers,
                }
            else:
                await self._codex_client.question_reject(request_id, directory=directory)
                result = {
                    "ok": True,
                    "request_id": request_id,
                }
            self._codex_client.discard_interrupt_request(request_id)
        except InterruptRequestError as exc:
            return self._interrupt_error_from_exception(base_request.id, exc)
        except httpx.HTTPStatusError as exc:
            upstream_status = exc.response.status_code
            if upstream_status == 404:
                self._codex_client.discard_interrupt_request(request_id)
                return self._interrupt_error_response(
                    base_request.id,
                    code=ERR_INTERRUPT_NOT_FOUND,
                    message="Interrupt request not found",
                    data={
                        "type": "INTERRUPT_REQUEST_NOT_FOUND",
                        "request_id": request_id,
                    },
                )
            return self._generate_error_response(
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
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_UNREACHABLE,
                    message="Upstream Codex unreachable",
                    data={"type": "UPSTREAM_UNREACHABLE", "request_id": request_id},
                ),
            )
        except Exception as exc:
            logger.exception("Codex interrupt callback JSON-RPC method failed")
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InternalError(message=str(exc))),
            )

        if base_request.id is None:
            return Response(status_code=204)
        return self._jsonrpc_success_response(base_request.id, result)

    async def _validate_interrupt_owner(
        self,
        *,
        request: Request,
        binding: InterruptRequestBinding | None,
        request_id: str,
        response_id: str | int | None,
    ) -> Response | None:
        identity = getattr(request.state, "user_identity", None)
        if not isinstance(identity, str) or not identity.strip():
            return None
        if binding is None or not binding.session_id or self._session_owner_matcher is None:
            return None
        matches = await self._session_owner_matcher(
            identity=identity.strip(),
            session_id=binding.session_id,
        )
        if matches is False:
            return self._interrupt_error_response(
                response_id,
                code=ERR_INTERRUPT_NOT_FOUND,
                message="Interrupt request not found",
                data={
                    "type": "INTERRUPT_REQUEST_NOT_FOUND",
                    "request_id": request_id,
                },
            )
        return None

    def _interrupt_error_from_exception(
        self,
        request_id: str | int | None,
        exc: InterruptRequestError,
    ) -> JSONResponse:
        if exc.error_type == "INTERRUPT_REQUEST_EXPIRED":
            return self._interrupt_error_response(
                request_id,
                code=ERR_INTERRUPT_EXPIRED,
                message="Interrupt request expired",
                data={
                    "type": exc.error_type,
                    "request_id": exc.request_id,
                },
            )
        if exc.error_type == "INTERRUPT_TYPE_MISMATCH":
            return self._interrupt_error_response(
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
        return self._interrupt_error_response(
            request_id,
            code=ERR_INTERRUPT_NOT_FOUND,
            message="Interrupt request not found",
            data={
                "type": "INTERRUPT_REQUEST_NOT_FOUND",
                "request_id": exc.request_id,
            },
        )

    def _interrupt_error_response(
        self,
        request_id: str | int | None,
        *,
        code: int,
        message: str,
        data: dict[str, Any],
    ) -> JSONResponse:
        return self._generate_error_response(
            request_id,
            JSONRPCError(
                code=code,
                message=message,
                data=data,
            ),
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
