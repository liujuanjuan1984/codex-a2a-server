from __future__ import annotations

import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from urllib.parse import unquote

import uvicorn
from a2a.server.apps.jsonrpc.jsonrpc_app import DefaultCallContextBuilder
from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
    AuthorizationCodeOAuthFlow,
    HTTPAuthSecurityScheme,
    OAuth2SecurityScheme,
    OAuthFlows,
    SecurityScheme,
    TransportProtocol,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from .agent import CodexAgentExecutor
from .codex_client import CodexClient
from .config import Settings
from .extension_contracts import (
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_CALLBACK_METHODS,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_CONTROL_METHODS,
    SESSION_QUERY_EXTENSION_URI,
    SESSION_QUERY_METHODS,
    STREAMING_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
    build_compatibility_profile_params,
    build_interrupt_callback_extension_params,
    build_session_binding_extension_params,
    build_session_query_extension_params,
    build_streaming_extension_params,
    build_supported_jsonrpc_methods,
    build_wire_contract_extension_params,
)
from .jsonrpc_ext import CodexSessionQueryJSONRPCApplication
from .logging_context import (
    CORRELATION_ID_HEADER,
    install_log_record_factory,
    reset_correlation_id,
    resolve_correlation_id,
    set_correlation_id,
)
from .openapi_contracts import patch_openapi_contract
from .request_handler import CodexRequestHandler

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext


class IdentityAwareCallContextBuilder(DefaultCallContextBuilder):
    def build(self, request: Request) -> ServerCallContext:
        context = super().build(request)
        path = request.url.path
        raw_path = request.scope.get("raw_path")
        raw_value = ""
        if isinstance(raw_path, (bytes, bytearray)):
            raw_value = raw_path.decode(errors="ignore")
        is_stream = (
            path.endswith("/v1/message:stream")
            or path.endswith("/v1/message%3Astream")
            or raw_value.endswith("/v1/message:stream")
            or raw_value.endswith("/v1/message%3Astream")
        )
        if is_stream:
            context.state["a2a_streaming_request"] = True

        identity = getattr(request.state, "user_identity", None)
        if identity:
            context.state["identity"] = identity
        correlation_id = getattr(request.state, "correlation_id", None)
        if isinstance(correlation_id, str) and correlation_id:
            context.state["correlation_id"] = correlation_id

        return context


def _build_deployment_context(settings: Settings) -> dict[str, str | bool | int]:
    context: dict[str, str | bool | int] = {
        "allow_directory_override": settings.a2a_allow_directory_override,
        "health_endpoint_enabled": settings.a2a_enable_health_endpoint,
        "interrupt_request_ttl_seconds": settings.a2a_interrupt_request_ttl_seconds,
        "session_shell_enabled": settings.a2a_enable_session_shell,
        "shared_workspace_across_consumers": True,
        "streaming_enabled": settings.a2a_streaming,
    }
    if settings.a2a_project:
        context["project"] = settings.a2a_project
    if settings.codex_directory:
        context["workspace_root"] = settings.codex_directory
    if settings.codex_provider_id:
        context["provider_id"] = settings.codex_provider_id
    if settings.codex_model_id:
        context["model_id"] = settings.codex_model_id
    if settings.codex_agent:
        context["agent"] = settings.codex_agent
    if settings.codex_variant:
        context["variant"] = settings.codex_variant
    return context


def _build_agent_card_description(
    settings: Settings, deployment_context: dict[str, str | bool | int]
) -> str:
    base = (settings.a2a_description or "").strip() or "A2A wrapper service for Codex."
    summary = (
        "Supports HTTP+JSON and JSON-RPC transports, standard A2A messaging "
        "(message/send, message/stream), task APIs (tasks/get, tasks/cancel, "
        "tasks/resubscribe; REST mapping: GET /v1/tasks/{id}:subscribe), "
        "shared session-binding and streaming contracts, Codex session-query "
        "extensions, shared interrupt callback extensions, a machine-readable "
        "compatibility profile, and a machine-readable wire contract."
    )
    parts: list[str] = [base, summary]
    parts.append(
        "Within one codex-a2a-server instance, all consumers share the same "
        "underlying Codex workspace/environment."
    )
    project = deployment_context.get("project")
    if isinstance(project, str) and project.strip():
        parts.append(f"Deployment project: {project}.")
    workspace_root = deployment_context.get("workspace_root")
    if isinstance(workspace_root, str) and workspace_root.strip():
        parts.append(f"Workspace root: {workspace_root}.")
    provider_id = deployment_context.get("provider_id")
    model_id = deployment_context.get("model_id")
    if isinstance(provider_id, str) and isinstance(model_id, str):
        parts.append(f"Default upstream model: {provider_id}/{model_id}.")
    return " ".join(parts)


def _build_chat_examples(project: str | None) -> list[str]:
    examples = [
        "Explain what this repository does.",
        "Summarize the API endpoints in this project.",
    ]
    if project:
        examples.append(f"Summarize current work items for project {project}.")
    return examples


def build_agent_card(settings: Settings) -> AgentCard:
    public_url = settings.a2a_public_url.rstrip("/")
    base_url = public_url
    deployment_context = _build_deployment_context(settings)
    session_binding_extension_params = build_session_binding_extension_params(
        deployment_context=deployment_context,
        directory_override_enabled=settings.a2a_allow_directory_override,
    )
    streaming_extension_params = build_streaming_extension_params()
    session_query_extension_params = build_session_query_extension_params(
        deployment_context=deployment_context,
        session_shell_enabled=settings.a2a_enable_session_shell,
    )
    interrupt_callback_extension_params = build_interrupt_callback_extension_params(
        deployment_context=deployment_context
    )
    wire_contract_extension_params = build_wire_contract_extension_params(
        protocol_version=settings.a2a_protocol_version,
        session_shell_enabled=settings.a2a_enable_session_shell,
    )
    compatibility_profile_params = build_compatibility_profile_params(
        protocol_version=settings.a2a_protocol_version,
        session_shell_enabled=settings.a2a_enable_session_shell,
    )
    security_schemes: dict[str, SecurityScheme] = {
        "bearerAuth": SecurityScheme(
            root=HTTPAuthSecurityScheme(
                description="Bearer token authentication",
                scheme="bearer",
                bearer_format="opaque",
            )
        )
    }
    security: list[dict[str, list[str]]] = [{"bearerAuth": []}]

    if settings.a2a_oauth_authorization_url and settings.a2a_oauth_token_url:
        security_schemes["oauth2"] = SecurityScheme(
            root=OAuth2SecurityScheme(
                oauth2_metadata_url=settings.a2a_oauth_metadata_url,
                flows=OAuthFlows(
                    authorization_code=AuthorizationCodeOAuthFlow(
                        authorization_url=settings.a2a_oauth_authorization_url,
                        token_url=settings.a2a_oauth_token_url,
                        refresh_url=None,
                        scopes=settings.a2a_oauth_scopes,
                    )
                ),
            )
        )
        security.append({"oauth2": list(settings.a2a_oauth_scopes.keys())})

    return AgentCard(
        name=settings.a2a_title,
        description=_build_agent_card_description(settings, deployment_context),
        url=base_url,
        documentation_url=settings.a2a_documentation_url,
        version=settings.a2a_version,
        protocol_version=settings.a2a_protocol_version,
        preferred_transport=TransportProtocol.http_json,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(
            streaming=settings.a2a_streaming,
            extensions=[
                AgentExtension(
                    uri=SESSION_BINDING_EXTENSION_URI,
                    required=False,
                    description=(
                        "Shared contract to bind A2A messages to an existing Codex session "
                        "when continuing a previous chat. Clients should pass "
                        "metadata.shared.session.id. The metadata.codex.directory field "
                        "remains available as a Codex-private override under "
                        "server-side directory boundary validation."
                    ),
                    params=session_binding_extension_params,
                ),
                AgentExtension(
                    uri=STREAMING_EXTENSION_URI,
                    required=False,
                    description=(
                        "Shared streaming metadata contract for canonical block hints, "
                        "timeline identity, usage, and interactive interrupt metadata."
                    ),
                    params=streaming_extension_params,
                ),
                AgentExtension(
                    uri=SESSION_QUERY_EXTENSION_URI,
                    required=False,
                    description=(
                        "Support Codex session list/history queries via custom JSON-RPC methods "
                        "on the agent's A2A JSON-RPC interface."
                    ),
                    params=session_query_extension_params,
                ),
                AgentExtension(
                    uri=INTERRUPT_CALLBACK_EXTENSION_URI,
                    required=False,
                    description=(
                        "Handle interactive interrupt callbacks generated during "
                        "streaming through shared JSON-RPC methods."
                    ),
                    params=interrupt_callback_extension_params,
                ),
                AgentExtension(
                    uri=COMPATIBILITY_PROFILE_EXTENSION_URI,
                    required=False,
                    description=(
                        "Machine-readable compatibility profile for the current A2A core "
                        "baseline, declared custom extensions, and retention policy."
                    ),
                    params=compatibility_profile_params,
                ),
                AgentExtension(
                    uri=WIRE_CONTRACT_EXTENSION_URI,
                    required=False,
                    description=(
                        "Declare the current JSON-RPC/HTTP method boundary and the "
                        "unsupported method error contract for generic A2A clients."
                    ),
                    params=wire_contract_extension_params,
                ),
            ],
        ),
        skills=[
            AgentSkill(
                id="codex.chat",
                name="Codex Chat",
                description=(
                    "Handle message/send and message/stream requests by routing user text to "
                    "Codex sessions."
                ),
                tags=["assistant", "coding", "codex"],
                examples=_build_chat_examples(settings.a2a_project),
            ),
            AgentSkill(
                id="codex.sessions.query",
                name="Codex Sessions Query",
                description=(
                    "Query Codex sessions and message histories via JSON-RPC extension "
                    "methods codex.sessions.list and codex.sessions.messages.list."
                ),
                tags=["codex", "sessions", "history"],
                examples=[
                    "List Codex sessions (method codex.sessions.list).",
                    "List messages for a session (method codex.sessions.messages.list).",
                ],
            ),
            AgentSkill(
                id="codex.interrupt.callback",
                name="Codex Interrupt Callback",
                description=(
                    "Reply permission/question interrupts emitted during streaming via "
                    "JSON-RPC methods a2a.interrupt.permission.reply, "
                    "a2a.interrupt.question.reply, and a2a.interrupt.question.reject."
                ),
                tags=["codex", "interrupt", "permission", "question", "shared"],
                examples=[
                    "Reply once/always/reject to a permission request by request_id.",
                    "Submit answers for a question request by request_id.",
                ],
            ),
        ],
        additional_interfaces=[
            AgentInterface(transport=TransportProtocol.http_json, url=base_url),
            AgentInterface(transport=TransportProtocol.jsonrpc, url=base_url),
        ],
        security_schemes=security_schemes,
        security=security,
    )


def add_auth_middleware(app: FastAPI, settings: Settings) -> None:
    token = settings.a2a_bearer_token

    def _unauthorized_response() -> JSONResponse:
        return JSONResponse(
            {"error": "Unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    public_paths = {
        "/.well-known/agent-card.json",
        "/.well-known/agent.json",
    }
    if settings.a2a_enable_health_endpoint:
        public_paths.add("/health")

    @app.middleware("http")
    async def bearer_auth(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in public_paths:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return _unauthorized_response()
        provided = auth_header.split(" ", 1)[1].strip()
        if not secrets.compare_digest(provided, token):
            return _unauthorized_response()

        return await call_next(request)


def create_app(settings: Settings) -> FastAPI:
    install_log_record_factory()
    client = CodexClient(settings)
    executor = CodexAgentExecutor(
        client,
        streaming_enabled=settings.a2a_streaming,
        session_cache_ttl_seconds=settings.a2a_session_cache_ttl_seconds,
        session_cache_maxsize=settings.a2a_session_cache_maxsize,
    )
    task_store = InMemoryTaskStore()
    handler = CodexRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await client.close()

    deployment_context = _build_deployment_context(settings)
    agent_card = build_agent_card(settings)
    context_builder = IdentityAwareCallContextBuilder()
    jsonrpc_methods = {
        **SESSION_QUERY_METHODS,
        **SESSION_CONTROL_METHODS,
        **INTERRUPT_CALLBACK_METHODS,
    }
    if not settings.a2a_enable_session_shell:
        jsonrpc_methods.pop("shell", None)

    # Build JSON-RPC app (POST / by default) and attach REST endpoints (HTTP+JSON) to the same app.
    app = CodexSessionQueryJSONRPCApplication(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=context_builder,
        codex_client=client,
        methods=jsonrpc_methods,
        protocol_version=settings.a2a_protocol_version,
        supported_methods=build_supported_jsonrpc_methods(
            session_shell_enabled=settings.a2a_enable_session_shell
        ),
        directory_resolver=executor.resolve_directory,
        session_claim=executor.claim_session,
        session_claim_finalize=executor.finalize_session_claim,
        session_claim_release=executor.release_session_claim,
        session_owner_matcher=executor.session_owner_matches,
    ).build(title=settings.a2a_title, version=settings.a2a_version, lifespan=lifespan)
    app.state.codex_client = client
    app.state.codex_executor = executor

    rest_adapter = RESTAdapter(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=context_builder,
    )
    for route, callback in rest_adapter.routes().items():
        app.add_api_route(route[0], callback, methods=[route[1]])

    if settings.a2a_enable_health_endpoint:

        @app.get("/health")
        async def health_check():
            return {
                "status": "ok",
                "service": "codex-a2a-server",
                "version": settings.a2a_version,
                "streaming_enabled": settings.a2a_streaming,
                "session_shell_enabled": settings.a2a_enable_session_shell,
                "interrupt_request_ttl_seconds": settings.a2a_interrupt_request_ttl_seconds,
            }

    def _parse_json_body(body_bytes: bytes) -> dict | None:
        try:
            payload = json.loads(body_bytes.decode("utf-8", errors="replace"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _detect_codex_extension_method(payload: dict | None) -> str | None:
        if payload is None:
            return None
        method = payload.get("method")
        if not isinstance(method, str):
            return None
        if method.startswith("codex."):
            return method
        return None

    def _parse_content_length(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    def _normalize_content_type(value: str | None) -> str:
        if not value:
            return ""
        return value.split(";", 1)[0].strip().lower()

    def _is_json_content_type(content_type: str) -> bool:
        if not content_type:
            return False
        return content_type == "application/json" or content_type.endswith("+json")

    def _decode_payload_preview(body: bytes, *, limit: int) -> str:
        text = body.decode("utf-8", errors="replace")
        if limit > 0 and len(text) > limit:
            return f"{text[:limit]}...[truncated]"
        return text

    async def _get_request_body(request: Request) -> bytes:
        body = await request.body()
        request._body = body  # allow downstream to read again
        return body

    def _looks_like_jsonrpc_message_payload(payload: dict | None) -> bool:
        if payload is None:
            return False
        message = payload.get("message")
        if not isinstance(message, dict):
            return False
        if "parts" in message:
            return True
        role = message.get("role")
        return isinstance(role, str) and role in {"user", "agent"}

    def _looks_like_jsonrpc_envelope(payload: dict | None) -> bool:
        if payload is None:
            return False
        method = payload.get("method")
        version = payload.get("jsonrpc")
        return isinstance(method, str) and isinstance(version, str)

    @app.middleware("http")
    async def guard_rest_payload_shape(request: Request, call_next):
        if request.method != "POST" or request.url.path not in {
            "/v1/message:send",
            "/v1/message:stream",
        }:
            return await call_next(request)

        body = await _get_request_body(request)
        payload = _parse_json_body(body)
        if _looks_like_jsonrpc_envelope(payload) or _looks_like_jsonrpc_message_payload(payload):
            return JSONResponse(
                {
                    "error": (
                        "Invalid HTTP+JSON payload for REST endpoint. "
                        "Use message.content with ROLE_* role values, or call "
                        "POST / with method=message/send or method=message/stream."
                    )
                },
                status_code=400,
            )
        return await call_next(request)

    @app.middleware("http")
    async def guard_missing_subscribe_task(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/v1/tasks/") or not path.endswith(":subscribe"):
            return await call_next(request)

        encoded_task_id = path.removeprefix("/v1/tasks/").removesuffix(":subscribe")
        task_id = unquote(encoded_task_id).strip()
        if not task_id:
            return JSONResponse({"error": "Task not found"}, status_code=404)

        task = await task_store.get(task_id)
        if task is None:
            return JSONResponse({"error": "Task not found", "task_id": task_id}, status_code=404)
        return await call_next(request)

    @app.middleware("http")
    async def log_payloads(request: Request, call_next):
        if not settings.a2a_log_payloads:
            return await call_next(request)

        path = request.url.path
        limit = settings.a2a_log_body_limit
        content_type = _normalize_content_type(request.headers.get("content-type"))
        content_length = _parse_content_length(request.headers.get("content-length"))

        sensitive_method: str | None = None
        request_omit_reason: str | None = None

        if not _is_json_content_type(content_type):
            request_omit_reason = f"non-json content-type={content_type or 'unknown'}"
        elif limit > 0 and content_length is None:
            request_omit_reason = f"missing content-length with limit={limit}"
        elif limit > 0 and content_length > limit:
            request_omit_reason = f"content-length={content_length} exceeds limit={limit}"
        else:
            body = await _get_request_body(request)
            payload = _parse_json_body(body)
            sensitive_method = _detect_codex_extension_method(payload)

            if sensitive_method:
                logger.debug("A2A request %s %s method=%s", request.method, path, sensitive_method)
            else:
                logger.debug(
                    "A2A request %s %s body=%s",
                    request.method,
                    path,
                    _decode_payload_preview(body, limit=limit),
                )

        if request_omit_reason:
            logger.debug(
                "A2A request %s %s body=[omitted %s]",
                request.method,
                path,
                request_omit_reason,
            )

        response = await call_next(request)
        if isinstance(response, StreamingResponse):
            if sensitive_method:
                logger.debug("A2A response %s streaming method=%s", path, sensitive_method)
            else:
                logger.debug("A2A response %s streaming", path)
            return response

        response_body = getattr(response, "body", b"") or b""
        if sensitive_method:
            logger.debug(
                "A2A response %s status=%s bytes=%s method=%s",
                path,
                response.status_code,
                len(response_body),
                sensitive_method,
            )
            return response

        if request_omit_reason:
            logger.debug(
                "A2A response %s status=%s bytes=%s body=[omitted request_%s]",
                path,
                response.status_code,
                len(response_body),
                request_omit_reason,
            )
            return response

        response_content_type = _normalize_content_type(response.headers.get("content-type"))
        if not _is_json_content_type(response_content_type):
            logger.debug(
                "A2A response %s status=%s bytes=%s body=[omitted non-json content-type=%s]",
                path,
                response.status_code,
                len(response_body),
                response_content_type or "unknown",
            )
            return response

        logger.debug(
            "A2A response %s status=%s body=%s",
            path,
            response.status_code,
            _decode_payload_preview(response_body, limit=limit),
        )
        return response

    add_auth_middleware(app, settings)

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        correlation_id = resolve_correlation_id(request.headers.get("x-request-id"))
        request.state.correlation_id = correlation_id
        token = set_correlation_id(correlation_id)
        started_at = time.perf_counter()
        path = request.url.path
        logger.info("A2A request started method=%s path=%s", request.method, path)
        try:
            response = await call_next(request)
            response.headers[CORRELATION_ID_HEADER] = correlation_id
            logger.info(
                "A2A request completed method=%s path=%s status=%s duration_ms=%.2f",
                request.method,
                path,
                response.status_code,
                (time.perf_counter() - started_at) * 1000.0,
            )
            return response
        except Exception:
            logger.exception(
                "A2A request failed method=%s path=%s duration_ms=%.2f",
                request.method,
                path,
                (time.perf_counter() - started_at) * 1000.0,
            )
            raise
        finally:
            reset_correlation_id(token)

    patch_openapi_contract(
        app,
        deployment_context=deployment_context,
        directory_override_enabled=settings.a2a_allow_directory_override,
        protocol_version=settings.a2a_protocol_version,
        session_shell_enabled=settings.a2a_enable_session_shell,
    )

    return app


def _normalize_log_level(value: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        return normalized
    return "INFO"


def _configure_logging(level: str) -> None:
    install_log_record_factory()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=(
            "%(asctime)s %(levelname)s %(name)s [correlation_id=%(correlation_id)s]: %(message)s"
        ),
    )
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)


def main() -> None:
    settings = Settings()
    app = create_app(settings)
    log_level = _normalize_log_level(settings.a2a_log_level)
    _configure_logging(log_level)
    uvicorn.run(app, host=settings.a2a_host, port=settings.a2a_port, log_level=log_level.lower())


if __name__ == "__main__":
    main()
