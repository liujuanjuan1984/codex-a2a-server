from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import uvicorn
from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPI
from a2a.server.apps.jsonrpc.jsonrpc_app import DefaultCallContextBuilder
from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityScheme,
    TransportProtocol,
)
from fastapi import FastAPI, Request

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
    build_capability_snapshot,
    build_compatibility_profile_params,
    build_interrupt_callback_extension_params,
    build_session_binding_extension_params,
    build_session_query_extension_params,
    build_streaming_extension_params,
    build_wire_contract_extension_params,
)
from .http_middlewares import install_http_middlewares
from .jsonrpc_ext import CodexSessionQueryJSONRPCApplication
from .logging_context import install_log_record_factory
from .openapi_contracts import patch_openapi_contract
from .profile import RuntimeProfile, build_runtime_profile
from .request_handler import CodexRequestHandler

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


def _build_agent_card_description(settings: Settings, runtime_profile: RuntimeProfile) -> str:
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
    parts.append(
        "Terminal tasks/resubscribe replay-once behavior is declared as a "
        "service-level contract for this deployment."
    )
    parts.append("This server profile is intended for single-tenant, self-hosted coding workflows.")
    runtime_context = runtime_profile.runtime_context
    project = runtime_context.project
    if isinstance(project, str) and project.strip():
        parts.append(f"Deployment project: {project}.")
    workspace_root = runtime_context.workspace_root
    if isinstance(workspace_root, str) and workspace_root.strip():
        parts.append(f"Workspace root: {workspace_root}.")
    provider_id = runtime_context.provider_id
    model_id = runtime_context.model_id
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


def build_agent_card(
    settings: Settings, *, runtime_profile: RuntimeProfile | None = None
) -> AgentCard:
    public_url = settings.a2a_public_url.rstrip("/")
    base_url = public_url
    runtime_profile = runtime_profile or build_runtime_profile(settings)
    session_binding_extension_params = build_session_binding_extension_params(
        runtime_profile=runtime_profile,
    )
    streaming_extension_params = build_streaming_extension_params()
    session_query_extension_params = build_session_query_extension_params(
        runtime_profile=runtime_profile,
    )
    interrupt_callback_extension_params = build_interrupt_callback_extension_params(
        runtime_profile=runtime_profile
    )
    wire_contract_extension_params = build_wire_contract_extension_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )
    compatibility_profile_params = build_compatibility_profile_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
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

    return AgentCard(
        name=settings.a2a_title,
        description=_build_agent_card_description(settings, runtime_profile),
        url=base_url,
        documentation_url=settings.a2a_documentation_url,
        version=settings.a2a_version,
        protocol_version=settings.a2a_protocol_version,
        preferred_transport=TransportProtocol.http_json,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(
            streaming=True,
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


def create_app(settings: Settings) -> FastAPI:
    install_log_record_factory()
    client = CodexClient(settings)
    executor = CodexAgentExecutor(
        client,
        streaming_enabled=True,
        cancel_abort_timeout_seconds=settings.a2a_cancel_abort_timeout_seconds,
        session_cache_ttl_seconds=settings.a2a_session_cache_ttl_seconds,
        session_cache_maxsize=settings.a2a_session_cache_maxsize,
        stream_idle_diagnostic_seconds=settings.a2a_stream_idle_diagnostic_seconds,
    )
    task_store = InMemoryTaskStore()
    handler = CodexRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        startup_preflight = getattr(client, "startup_preflight", None)
        if callable(startup_preflight):
            await startup_preflight()
        yield
        await client.close()

    runtime_profile = build_runtime_profile(settings)
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    agent_card = build_agent_card(settings, runtime_profile=runtime_profile)
    context_builder = IdentityAwareCallContextBuilder()
    jsonrpc_methods = {
        **SESSION_QUERY_METHODS,
        **SESSION_CONTROL_METHODS,
        **INTERRUPT_CALLBACK_METHODS,
    }
    if "shell" not in capability_snapshot.session_query_method_keys:
        jsonrpc_methods.pop("shell", None)

    # Compose the shared FastAPI app from the SDK JSON-RPC and REST application wrappers.
    jsonrpc_app = CodexSessionQueryJSONRPCApplication(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=context_builder,
        codex_client=client,
        methods=jsonrpc_methods,
        protocol_version=settings.a2a_protocol_version,
        supported_methods=list(capability_snapshot.supported_jsonrpc_methods),
        directory_resolver=executor.resolve_directory,
        session_claim=executor.claim_session,
        session_claim_finalize=executor.finalize_session_claim,
        session_claim_release=executor.release_session_claim,
        session_owner_matcher=executor.session_owner_matches,
    )
    app = A2AFastAPI(
        title=settings.a2a_title,
        version=settings.a2a_version,
        lifespan=lifespan,
    )
    jsonrpc_app.add_routes_to_app(app)
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
            return runtime_profile.health_payload(
                service="codex-a2a-server",
                version=settings.a2a_version,
            )

    install_http_middlewares(
        app,
        settings=settings,
        task_store=task_store,
    )

    patch_openapi_contract(
        app,
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )

    app_status_cls: Any | None = None
    try:
        from sse_starlette.sse import AppStatus as app_status_cls
    except ImportError:  # pragma: no cover - optional dependency
        pass
    if app_status_cls is not None:
        app_status_cls.should_exit = False
        app_status_cls.should_exit_event = None

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
    settings = Settings.from_env()
    app = create_app(settings)
    log_level = _normalize_log_level(settings.a2a_log_level)
    _configure_logging(log_level)
    uvicorn.run(app, host=settings.a2a_host, port=settings.a2a_port, log_level=log_level.lower())


if __name__ == "__main__":
    main()
