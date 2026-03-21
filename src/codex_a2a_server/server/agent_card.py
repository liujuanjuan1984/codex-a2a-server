from __future__ import annotations

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

from codex_a2a_server.config import Settings
from codex_a2a_server.contracts.extensions import (
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_QUERY_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
    build_compatibility_profile_params,
    build_interrupt_callback_extension_params,
    build_session_binding_extension_params,
    build_session_query_extension_params,
    build_streaming_extension_params,
    build_wire_contract_extension_params,
)
from codex_a2a_server.profile.runtime import RuntimeProfile, build_runtime_profile


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
