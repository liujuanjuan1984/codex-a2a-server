from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .profile import RuntimeProfile

COMPATIBILITY_PROFILE_EXTENSION_URI = "urn:codex-a2a:compatibility-profile/v1"
WIRE_CONTRACT_EXTENSION_URI = "urn:codex-a2a:wire-contract/v1"
SESSION_BINDING_EXTENSION_URI = "urn:a2a:session-binding/v1"
STREAMING_EXTENSION_URI = "urn:a2a:stream-hints/v1"
SESSION_QUERY_EXTENSION_URI = "urn:codex-a2a:codex-session-query/v1"
INTERRUPT_CALLBACK_EXTENSION_URI = "urn:a2a:interactive-interrupt/v1"

SHARED_METADATA_NAMESPACE = "shared"
SHARED_SESSION_BINDING_FIELD = "metadata.shared.session.id"
SHARED_SESSION_METADATA_FIELD = "metadata.shared.session"
SHARED_STREAM_METADATA_FIELD = "metadata.shared.stream"
SHARED_INTERRUPT_METADATA_FIELD = "metadata.shared.interrupt"
SHARED_USAGE_METADATA_FIELD = "metadata.shared.usage"
CODEX_DIRECTORY_METADATA_FIELD = "metadata.codex.directory"


@dataclass(frozen=True)
class SessionQueryMethodContract:
    method: str
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    unsupported_params: tuple[str, ...] = ()
    result_fields: tuple[str, ...] = ()
    items_type: str | None = None
    items_field: str | None = None
    notification_response_status: int | None = None
    pagination_mode: str | None = None
    execution_binding: str | None = None
    session_binding: str | None = None
    uses_upstream_session_context: bool | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class InterruptMethodContract:
    method: str
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    notification_response_status: int | None = None


SESSION_QUERY_PAGINATION_MODE = "limit"
SESSION_QUERY_PAGINATION_BEHAVIOR = "mixed"
SESSION_QUERY_DEFAULT_LIMIT = 20
SESSION_QUERY_MAX_LIMIT = 100
SESSION_QUERY_PAGINATION_PARAMS: tuple[str, ...] = ("limit",)
SESSION_QUERY_PAGINATION_UNSUPPORTED: tuple[str, ...] = ("cursor", "page", "size")

SESSION_QUERY_METHOD_CONTRACTS: dict[str, SessionQueryMethodContract] = {
    "list_sessions": SessionQueryMethodContract(
        method="codex.sessions.list",
        optional_params=("limit", "query.limit"),
        unsupported_params=SESSION_QUERY_PAGINATION_UNSUPPORTED,
        result_fields=("items",),
        items_type="Task[]",
        items_field="items",
        notification_response_status=204,
        pagination_mode=SESSION_QUERY_PAGINATION_MODE,
    ),
    "get_session_messages": SessionQueryMethodContract(
        method="codex.sessions.messages.list",
        required_params=("session_id",),
        optional_params=("limit", "query.limit"),
        unsupported_params=SESSION_QUERY_PAGINATION_UNSUPPORTED,
        result_fields=("items",),
        items_type="Message[]",
        items_field="items",
        notification_response_status=204,
        pagination_mode=SESSION_QUERY_PAGINATION_MODE,
    ),
    "prompt_async": SessionQueryMethodContract(
        method="codex.sessions.prompt_async",
        required_params=("session_id", "request.parts"),
        optional_params=(
            "request.messageID",
            "request.agent",
            "request.system",
            "request.variant",
            CODEX_DIRECTORY_METADATA_FIELD,
        ),
        result_fields=("ok", "session_id", "turn_id"),
        notification_response_status=204,
    ),
    "command": SessionQueryMethodContract(
        method="codex.sessions.command",
        required_params=("session_id", "request.command"),
        optional_params=(
            "request.arguments",
            "request.messageID",
            CODEX_DIRECTORY_METADATA_FIELD,
        ),
        result_fields=("item",),
        notification_response_status=204,
    ),
    "shell": SessionQueryMethodContract(
        method="codex.sessions.shell",
        required_params=("session_id", "request.command"),
        optional_params=(CODEX_DIRECTORY_METADATA_FIELD,),
        result_fields=("item",),
        notification_response_status=204,
        execution_binding="standalone_command_exec",
        session_binding="ownership_attribution_only",
        uses_upstream_session_context=False,
        notes=(
            (
                "Shell requests run through Codex command/exec and do not resume or "
                "create an upstream thread."
            ),
            (
                "session_id is used for ownership checks and A2A result attribution; "
                "it does not provide an upstream session-bound shell context."
            ),
        ),
    ),
}

SESSION_QUERY_METHODS: dict[str, str] = {
    key: contract.method for key, contract in SESSION_QUERY_METHOD_CONTRACTS.items()
}
SESSION_CONTROL_METHOD_KEYS: tuple[str, ...] = ("prompt_async", "command", "shell")
SESSION_CONTROL_METHODS: dict[str, str] = {
    key: SESSION_QUERY_METHODS[key] for key in SESSION_CONTROL_METHOD_KEYS
}

SESSION_QUERY_ERROR_BUSINESS_CODES: dict[str, int] = {
    "SESSION_NOT_FOUND": -32001,
    "SESSION_FORBIDDEN": -32006,
    "UPSTREAM_UNREACHABLE": -32002,
    "UPSTREAM_HTTP_ERROR": -32003,
    "UPSTREAM_PAYLOAD_ERROR": -32005,
}
SESSION_QUERY_ERROR_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "method",
    "session_id",
    "upstream_status",
    "detail",
)
SESSION_QUERY_INVALID_PARAMS_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "field",
    "fields",
    "supported",
    "unsupported",
)

INTERRUPT_CALLBACK_METHOD_CONTRACTS: dict[str, InterruptMethodContract] = {
    "reply_permission": InterruptMethodContract(
        method="a2a.interrupt.permission.reply",
        required_params=("request_id", "reply"),
        optional_params=("message", "metadata"),
        notification_response_status=204,
    ),
    "reply_question": InterruptMethodContract(
        method="a2a.interrupt.question.reply",
        required_params=("request_id", "answers"),
        optional_params=("metadata",),
        notification_response_status=204,
    ),
    "reject_question": InterruptMethodContract(
        method="a2a.interrupt.question.reject",
        required_params=("request_id",),
        optional_params=("metadata",),
        notification_response_status=204,
    ),
}

INTERRUPT_CALLBACK_METHODS: dict[str, str] = {
    key: contract.method for key, contract in INTERRUPT_CALLBACK_METHOD_CONTRACTS.items()
}

INTERRUPT_SUCCESS_RESULT_FIELDS: tuple[str, ...] = ("ok", "request_id")
INTERRUPT_ERROR_BUSINESS_CODES: dict[str, int] = {
    "INTERRUPT_REQUEST_NOT_FOUND": -32004,
    "INTERRUPT_REQUEST_EXPIRED": -32007,
    "INTERRUPT_TYPE_MISMATCH": -32008,
    "UPSTREAM_UNREACHABLE": -32002,
    "UPSTREAM_HTTP_ERROR": -32003,
}
INTERRUPT_ERROR_TYPES: tuple[str, ...] = (
    "INTERRUPT_REQUEST_NOT_FOUND",
    "INTERRUPT_REQUEST_EXPIRED",
    "INTERRUPT_TYPE_MISMATCH",
    "UPSTREAM_UNREACHABLE",
    "UPSTREAM_HTTP_ERROR",
)
INTERRUPT_ERROR_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "request_id",
    "expected_interrupt_type",
    "actual_interrupt_type",
    "upstream_status",
)
INTERRUPT_INVALID_PARAMS_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "field",
    "fields",
    "request_id",
)

CORE_JSONRPC_METHODS: tuple[str, ...] = (
    "message/send",
    "message/stream",
    "tasks/get",
    "tasks/cancel",
    "tasks/resubscribe",
)
CORE_HTTP_ENDPOINTS: tuple[str, ...] = (
    "/v1/message:send",
    "/v1/message:stream",
    "/v1/tasks/{id}:subscribe",
)
WIRE_CONTRACT_UNSUPPORTED_METHOD_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "method",
    "supported_methods",
    "protocol_version",
)


@dataclass(frozen=True)
class CapabilitySnapshot:
    supported_jsonrpc_methods: tuple[str, ...]
    extension_jsonrpc_methods: tuple[str, ...]
    session_query_method_keys: tuple[str, ...]
    session_query_methods: tuple[str, ...]
    conditional_methods: dict[str, dict[str, str]]


def build_capability_snapshot(*, runtime_profile: RuntimeProfile) -> CapabilitySnapshot:
    session_query_method_keys = [
        "list_sessions",
        "get_session_messages",
        "prompt_async",
        "command",
    ]
    conditional_methods: dict[str, dict[str, str]] = {}
    if runtime_profile.session_shell_enabled:
        session_query_method_keys.append("shell")
    else:
        conditional_methods[SESSION_CONTROL_METHODS["shell"]] = {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_SESSION_SHELL",
        }
    session_query_methods = tuple(SESSION_QUERY_METHODS[key] for key in session_query_method_keys)
    extension_jsonrpc_methods = (
        *session_query_methods,
        *INTERRUPT_CALLBACK_METHODS.values(),
    )
    return CapabilitySnapshot(
        supported_jsonrpc_methods=(
            *CORE_JSONRPC_METHODS,
            *extension_jsonrpc_methods,
        ),
        extension_jsonrpc_methods=extension_jsonrpc_methods,
        session_query_method_keys=tuple(session_query_method_keys),
        session_query_methods=session_query_methods,
        conditional_methods=conditional_methods,
    )


def build_supported_jsonrpc_methods(*, runtime_profile: RuntimeProfile) -> list[str]:
    snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    return list(snapshot.supported_jsonrpc_methods)


def build_wire_contract_extension_params(
    *,
    protocol_version: str,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    return {
        "protocol_version": protocol_version,
        "preferred_transport": "HTTP+JSON",
        "additional_transports": ["JSON-RPC"],
        "core": {
            "jsonrpc_methods": list(CORE_JSONRPC_METHODS),
            "http_endpoints": list(CORE_HTTP_ENDPOINTS),
        },
        "extensions": {
            "jsonrpc_methods": list(snapshot.extension_jsonrpc_methods),
            "conditionally_available_methods": dict(snapshot.conditional_methods),
            "extension_uris": [
                SESSION_BINDING_EXTENSION_URI,
                STREAMING_EXTENSION_URI,
                SESSION_QUERY_EXTENSION_URI,
                INTERRUPT_CALLBACK_EXTENSION_URI,
            ],
        },
        "all_jsonrpc_methods": list(snapshot.supported_jsonrpc_methods),
        "unsupported_method_error": {
            "code": -32601,
            "type": "METHOD_NOT_SUPPORTED",
            "data_fields": list(WIRE_CONTRACT_UNSUPPORTED_METHOD_DATA_FIELDS),
        },
    }


def build_compatibility_profile_params(
    *,
    protocol_version: str,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    snapshot = build_capability_snapshot(runtime_profile=runtime_profile)

    method_retention: dict[str, dict[str, Any]] = {
        method: {
            "surface": "core",
            "availability": "always",
            "retention": "required",
        }
        for method in CORE_JSONRPC_METHODS
    }
    method_retention.update(
        {
            method: {
                "surface": "extension",
                "availability": "always",
                "retention": "stable",
                "extension_uri": SESSION_QUERY_EXTENSION_URI,
            }
            for method in snapshot.session_query_methods
        }
    )
    method_retention[SESSION_CONTROL_METHODS["shell"]] = {
        "surface": "extension",
        "availability": ("enabled" if runtime_profile.session_shell_enabled else "disabled"),
        "retention": "deployment-conditional",
        "extension_uri": SESSION_QUERY_EXTENSION_URI,
        "toggle": "A2A_ENABLE_SESSION_SHELL",
    }
    method_retention.update(
        {
            method: {
                "surface": "extension",
                "availability": "always",
                "retention": "stable",
                "extension_uri": INTERRUPT_CALLBACK_EXTENSION_URI,
            }
            for method in INTERRUPT_CALLBACK_METHODS.values()
        }
    )

    extension_retention = {
        SESSION_BINDING_EXTENSION_URI: {
            "surface": "core-runtime-metadata",
            "availability": "always",
            "retention": "required",
        },
        STREAMING_EXTENSION_URI: {
            "surface": "core-runtime-metadata",
            "availability": "always",
            "retention": "required",
        },
        SESSION_QUERY_EXTENSION_URI: {
            "surface": "jsonrpc-extension",
            "availability": "always",
            "retention": "stable",
        },
        INTERRUPT_CALLBACK_EXTENSION_URI: {
            "surface": "jsonrpc-extension",
            "availability": "always",
            "retention": "stable",
        },
    }

    return {
        "profile_id": runtime_profile.profile_id,
        "protocol_version": protocol_version,
        "deployment": runtime_profile.deployment.as_dict(),
        "runtime_features": runtime_profile.runtime_features_dict(),
        "core": {
            "jsonrpc_methods": list(CORE_JSONRPC_METHODS),
            "http_endpoints": list(CORE_HTTP_ENDPOINTS),
        },
        "extension_taxonomy": {
            "shared_extensions": [
                SESSION_BINDING_EXTENSION_URI,
                STREAMING_EXTENSION_URI,
                INTERRUPT_CALLBACK_EXTENSION_URI,
            ],
            "codex_extensions": [
                SESSION_QUERY_EXTENSION_URI,
                COMPATIBILITY_PROFILE_EXTENSION_URI,
                WIRE_CONTRACT_EXTENSION_URI,
            ],
            "provider_private_metadata": ["codex.directory"],
        },
        "extension_retention": extension_retention,
        "method_retention": method_retention,
        "consumer_guidance": [
            "Treat core A2A methods as the stable interoperability baseline for generic clients.",
            (
                "Treat this deployment as a single-tenant, shared-workspace coding profile; "
                "do not assume per-consumer workspace or tenant isolation."
            ),
            (
                "Treat urn:a2a:* extension URIs in this repository as shared extension "
                "conventions used across this repo family, not as claims that they are part "
                "of the A2A core baseline."
            ),
            (
                "Treat shared session-binding, stream-hints, and interrupt callback surfaces "
                "as shared extensions rather than provider-private Codex capabilities."
            ),
            (
                "Treat codex.* methods and codex.directory metadata as Codex-specific "
                "extensions or provider-private operational surfaces rather than portable "
                "A2A baseline capabilities."
            ),
            (
                "codex.sessions.shell is deployment-conditional: discover it from the "
                "declared profile and current extension contracts before calling it."
            ),
        ],
    }


def _build_method_contract_params(
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    unsupported: tuple[str, ...],
) -> dict[str, list[str]]:
    params: dict[str, list[str]] = {}
    if required:
        params["required"] = list(required)
    if optional:
        params["optional"] = list(optional)
    if unsupported:
        params["unsupported"] = list(unsupported)
    return params


def build_session_binding_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    return {
        "metadata_field": SHARED_SESSION_BINDING_FIELD,
        "behavior": "prefer_metadata_binding_else_create_session",
        "supported_metadata": [
            "shared.session.id",
            "codex.directory",
        ],
        "provider_private_metadata": ["codex.directory"],
        "profile": runtime_profile.summary_dict(),
        "notes": [
            (
                "If metadata.shared.session.id is provided, the server will send the "
                "message to that upstream session."
            ),
            (
                "Otherwise, the server will create a new upstream session and cache "
                "the (identity, contextId)->session_id mapping in memory with TTL."
            ),
        ],
    }


def build_streaming_extension_params() -> dict[str, Any]:
    return {
        "artifact_metadata_field": SHARED_STREAM_METADATA_FIELD,
        "status_metadata_field": SHARED_STREAM_METADATA_FIELD,
        "interrupt_metadata_field": SHARED_INTERRUPT_METADATA_FIELD,
        "session_metadata_field": SHARED_SESSION_METADATA_FIELD,
        "usage_metadata_field": SHARED_USAGE_METADATA_FIELD,
        "block_types": ["text", "reasoning", "tool_call"],
        "stream_fields": {
            "block_type": f"{SHARED_STREAM_METADATA_FIELD}.block_type",
            "source": f"{SHARED_STREAM_METADATA_FIELD}.source",
            "message_id": f"{SHARED_STREAM_METADATA_FIELD}.message_id",
            "event_id": f"{SHARED_STREAM_METADATA_FIELD}.event_id",
            "sequence": f"{SHARED_STREAM_METADATA_FIELD}.sequence",
            "role": f"{SHARED_STREAM_METADATA_FIELD}.role",
        },
        "interrupt_fields": {
            "request_id": f"{SHARED_INTERRUPT_METADATA_FIELD}.request_id",
            "type": f"{SHARED_INTERRUPT_METADATA_FIELD}.type",
            "phase": f"{SHARED_INTERRUPT_METADATA_FIELD}.phase",
            "resolution": f"{SHARED_INTERRUPT_METADATA_FIELD}.resolution",
            "details": f"{SHARED_INTERRUPT_METADATA_FIELD}.details",
        },
        "usage_fields": {
            "input_tokens": f"{SHARED_USAGE_METADATA_FIELD}.input_tokens",
            "output_tokens": f"{SHARED_USAGE_METADATA_FIELD}.output_tokens",
            "total_tokens": f"{SHARED_USAGE_METADATA_FIELD}.total_tokens",
            "cost": f"{SHARED_USAGE_METADATA_FIELD}.cost",
        },
    }


def build_session_query_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    active_method_contracts = {
        key: contract
        for key, contract in SESSION_QUERY_METHOD_CONTRACTS.items()
        if key in snapshot.session_query_method_keys
    }
    active_query_methods = {
        key: contract.method for key, contract in active_method_contracts.items()
    }
    active_control_methods = {
        key: active_query_methods[key]
        for key in SESSION_CONTROL_METHOD_KEYS
        if key in active_query_methods
    }
    method_contracts: dict[str, Any] = {}
    pagination_applies_to: list[str] = []
    pagination_behavior_by_method: dict[str, str] = {}

    for method_contract in active_method_contracts.values():
        params_contract = _build_method_contract_params(
            required=method_contract.required_params,
            optional=method_contract.optional_params,
            unsupported=method_contract.unsupported_params,
        )
        result_contract: dict[str, Any] = {"fields": list(method_contract.result_fields)}
        if method_contract.items_type:
            result_contract["items_type"] = method_contract.items_type
        if method_contract.items_field:
            result_contract["items_field"] = method_contract.items_field

        contract_doc: dict[str, Any] = {
            "params": params_contract,
            "result": result_contract,
        }
        if method_contract.notification_response_status is not None:
            contract_doc["notification_response_status"] = (
                method_contract.notification_response_status
            )
        if method_contract.execution_binding is not None:
            contract_doc["execution_binding"] = method_contract.execution_binding
        if method_contract.session_binding is not None:
            contract_doc["session_binding"] = method_contract.session_binding
        if method_contract.uses_upstream_session_context is not None:
            contract_doc["uses_upstream_session_context"] = (
                method_contract.uses_upstream_session_context
            )
        if method_contract.notes:
            contract_doc["notes"] = list(method_contract.notes)
        method_contracts[method_contract.method] = contract_doc

        if method_contract.pagination_mode == SESSION_QUERY_PAGINATION_MODE:
            pagination_applies_to.append(method_contract.method)
            if method_contract.method == SESSION_QUERY_METHODS["list_sessions"]:
                pagination_behavior_by_method[method_contract.method] = "upstream_passthrough"
            elif method_contract.method == SESSION_QUERY_METHODS["get_session_messages"]:
                pagination_behavior_by_method[method_contract.method] = "local_tail_slice"

    return {
        "methods": active_query_methods,
        "control_methods": active_control_methods,
        "profile": runtime_profile.summary_dict(),
        "supported_metadata": ["codex.directory"],
        "provider_private_metadata": ["codex.directory"],
        "pagination": {
            "mode": SESSION_QUERY_PAGINATION_MODE,
            "default_limit": SESSION_QUERY_DEFAULT_LIMIT,
            "max_limit": SESSION_QUERY_MAX_LIMIT,
            "behavior": SESSION_QUERY_PAGINATION_BEHAVIOR,
            "by_method": pagination_behavior_by_method,
            "params": list(SESSION_QUERY_PAGINATION_PARAMS),
            "applies_to": pagination_applies_to,
            "notes": [
                "codex.sessions.list forwards limit upstream to Codex thread/list",
                (
                    "codex.sessions.messages.list reads the full thread history first and "
                    "then keeps the most recent N mapped messages locally"
                ),
            ],
        },
        "method_contracts": method_contracts,
        "errors": {
            "business_codes": dict(SESSION_QUERY_ERROR_BUSINESS_CODES),
            "error_data_fields": list(SESSION_QUERY_ERROR_DATA_FIELDS),
            "invalid_params_data_fields": list(SESSION_QUERY_INVALID_PARAMS_DATA_FIELDS),
        },
        "result_envelope": {},
        "context_semantics": {
            "a2a_context_id_field": "contextId",
            "upstream_session_id_field": SHARED_SESSION_BINDING_FIELD,
            "context_id_strategy": "equals_upstream_session_id",
            "notes": [
                (
                    "session query projections currently set contextId equal to the "
                    "upstream session_id"
                ),
                "metadata.shared.session.id carries the same upstream session identity explicitly",
            ],
        },
    }


def build_interrupt_callback_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    method_contracts: dict[str, Any] = {}
    for contract in INTERRUPT_CALLBACK_METHOD_CONTRACTS.values():
        method_contract_doc: dict[str, Any] = {
            "params": _build_method_contract_params(
                required=contract.required_params,
                optional=contract.optional_params,
                unsupported=(),
            ),
            "result": {"fields": list(INTERRUPT_SUCCESS_RESULT_FIELDS)},
        }
        if contract.notification_response_status is not None:
            method_contract_doc["notification_response_status"] = (
                contract.notification_response_status
            )
        method_contracts[contract.method] = method_contract_doc

    return {
        "methods": dict(INTERRUPT_CALLBACK_METHODS),
        "method_contracts": method_contracts,
        "supported_interrupt_events": [
            "permission.asked",
            "question.asked",
        ],
        "permission_reply_values": ["once", "always", "reject"],
        "question_reply_contract": {
            "answers": "array of answer arrays (same order as asked questions)"
        },
        "request_id_field": f"{SHARED_INTERRUPT_METADATA_FIELD}.request_id",
        "supported_metadata": ["codex.directory"],
        "provider_private_metadata": ["codex.directory"],
        "context_fields": {
            "directory": CODEX_DIRECTORY_METADATA_FIELD,
        },
        "success_result_fields": list(INTERRUPT_SUCCESS_RESULT_FIELDS),
        "errors": {
            "business_codes": dict(INTERRUPT_ERROR_BUSINESS_CODES),
            "error_types": list(INTERRUPT_ERROR_TYPES),
            "error_data_fields": list(INTERRUPT_ERROR_DATA_FIELDS),
            "invalid_params_data_fields": list(INTERRUPT_INVALID_PARAMS_DATA_FIELDS),
        },
        "profile": runtime_profile.summary_dict(),
    }
