from codex_a2a_server.app import (
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_QUERY_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
    build_agent_card,
)
from codex_a2a_server.extension_contracts import (
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
)
from tests.helpers import make_settings


def test_agent_card_description_reflects_actual_transport_capabilities() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token"))

    assert "HTTP+JSON and JSON-RPC transports" in card.description
    assert "message/send, message/stream" in card.description
    assert "tasks/get, tasks/cancel" in card.description
    assert "machine-readable wire contract" in card.description
    assert "machine-readable compatibility profile" in card.description
    assert "all consumers share the same underlying Codex workspace/environment" in card.description
    assert "single-tenant, self-hosted coding workflows" in card.description


def test_agent_card_declares_bearer_only_security() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token"))

    assert set((card.security_schemes or {}).keys()) == {"bearerAuth"}
    assert card.security == [{"bearerAuth": []}]


def test_agent_card_injects_profile_into_extensions() -> None:
    card = build_agent_card(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_project="alpha",
            codex_workspace_root="/srv/workspaces/alpha",
            codex_provider_id="google",
            codex_model_id="gemini-2.5-flash",
            codex_agent="code-reviewer",
            codex_variant="safe",
            a2a_allow_directory_override=False,
        )
    )
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}

    binding = ext_by_uri[SESSION_BINDING_EXTENSION_URI]
    profile = binding.params["profile"]
    assert profile["profile_id"] == "codex-a2a-single-tenant-coding-v1"
    assert profile["deployment"] == {
        "id": "single_tenant_shared_workspace",
        "single_tenant": True,
        "shared_workspace_across_consumers": True,
        "tenant_isolation": "none",
    }
    assert profile["runtime_context"] == {
        "project": "alpha",
        "workspace_root": "/srv/workspaces/alpha",
        "provider_id": "google",
        "model_id": "gemini-2.5-flash",
        "agent": "code-reviewer",
        "variant": "safe",
    }
    assert profile["runtime_features"]["directory_binding"] == {
        "allow_override": False,
        "scope": "workspace_root_only",
    }
    assert profile["runtime_features"]["session_shell"] == {
        "enabled": True,
        "availability": "enabled",
        "toggle": "A2A_ENABLE_SESSION_SHELL",
    }
    assert profile["runtime_features"]["interrupts"] == {
        "request_ttl_seconds": 3600,
    }
    assert binding.params["metadata_field"] == "metadata.shared.session.id"
    assert binding.params["supported_metadata"] == [
        "shared.session.id",
        "codex.directory",
    ]
    assert binding.params["provider_private_metadata"] == ["codex.directory"]

    streaming = ext_by_uri[STREAMING_EXTENSION_URI]
    assert streaming.params["artifact_metadata_field"] == "metadata.shared.stream"
    assert streaming.params["interrupt_metadata_field"] == "metadata.shared.interrupt"
    assert streaming.params["usage_metadata_field"] == "metadata.shared.usage"
    assert streaming.params["stream_fields"]["sequence"] == "metadata.shared.stream.sequence"
    assert streaming.params["interrupt_fields"]["phase"] == "metadata.shared.interrupt.phase"
    assert (
        streaming.params["interrupt_fields"]["resolution"] == "metadata.shared.interrupt.resolution"
    )

    session_query = ext_by_uri[SESSION_QUERY_EXTENSION_URI]
    assert session_query.params["profile"] == profile
    assert session_query.params["supported_metadata"] == ["codex.directory"]
    assert session_query.params["provider_private_metadata"] == ["codex.directory"]
    assert session_query.params["pagination"]["mode"] == "limit"
    assert session_query.params["pagination"]["default_limit"] == SESSION_QUERY_DEFAULT_LIMIT
    assert session_query.params["pagination"]["max_limit"] == SESSION_QUERY_MAX_LIMIT
    assert session_query.params["pagination"]["behavior"] == "mixed"
    assert session_query.params["pagination"]["by_method"] == {
        "codex.sessions.list": "upstream_passthrough",
        "codex.sessions.messages.list": "local_tail_slice",
    }
    assert session_query.params["result_envelope"] == {}
    assert any(
        "forwards limit upstream" in note for note in session_query.params["pagination"]["notes"]
    )
    assert (
        session_query.params["context_semantics"]["upstream_session_id_field"]
        == "metadata.shared.session.id"
    )
    assert session_query.params["context_semantics"]["context_id_strategy"] == (
        "equals_upstream_session_id"
    )
    assert any(
        "contextId equal to the upstream session_id" in note
        for note in session_query.params["context_semantics"]["notes"]
    )
    shell_contract = session_query.params["method_contracts"]["codex.sessions.shell"]
    assert shell_contract["execution_binding"] == "standalone_command_exec"
    assert shell_contract["session_binding"] == "ownership_attribution_only"
    assert shell_contract["uses_upstream_session_context"] is False
    assert any("command/exec" in note for note in shell_contract["notes"])

    interrupt = ext_by_uri[INTERRUPT_CALLBACK_EXTENSION_URI]
    assert interrupt.params["profile"] == profile
    assert interrupt.params["request_id_field"] == "metadata.shared.interrupt.request_id"
    assert interrupt.params["supported_metadata"] == ["codex.directory"]
    assert interrupt.params["provider_private_metadata"] == ["codex.directory"]
    assert interrupt.params["errors"]["business_codes"]["INTERRUPT_REQUEST_EXPIRED"] == -32007
    assert interrupt.params["errors"]["business_codes"]["INTERRUPT_TYPE_MISMATCH"] == -32008
    assert "expected_interrupt_type" in interrupt.params["errors"]["error_data_fields"]
    assert "actual_interrupt_type" in interrupt.params["errors"]["error_data_fields"]

    wire_contract = ext_by_uri[WIRE_CONTRACT_EXTENSION_URI]
    assert wire_contract.params["protocol_version"] == "0.3.0"
    compatibility = ext_by_uri[COMPATIBILITY_PROFILE_EXTENSION_URI]
    assert compatibility.params["profile_id"] == "codex-a2a-single-tenant-coding-v1"
    assert compatibility.params["protocol_version"] == "0.3.0"
    assert compatibility.params["deployment"] == {
        "id": "single_tenant_shared_workspace",
        "single_tenant": True,
        "shared_workspace_across_consumers": True,
        "tenant_isolation": "none",
    }
    assert compatibility.params["deployment"] == profile["deployment"]
    assert compatibility.params["runtime_features"] == profile["runtime_features"]
    assert compatibility.params["core"]["jsonrpc_methods"] == [
        "message/send",
        "message/stream",
        "tasks/get",
        "tasks/cancel",
        "tasks/resubscribe",
    ]
    assert compatibility.params["extension_taxonomy"]["shared_extensions"] == [
        "urn:a2a:session-binding/v1",
        "urn:a2a:stream-hints/v1",
        "urn:a2a:interactive-interrupt/v1",
    ]
    assert compatibility.params["extension_taxonomy"]["codex_extensions"] == [
        "urn:codex-a2a:codex-session-query/v1",
        "urn:codex-a2a:compatibility-profile/v1",
        "urn:codex-a2a:wire-contract/v1",
    ]
    assert compatibility.params["extension_taxonomy"]["provider_private_metadata"] == [
        "codex.directory"
    ]
    assert wire_contract.params["core"]["jsonrpc_methods"] == [
        "message/send",
        "message/stream",
        "tasks/get",
        "tasks/cancel",
        "tasks/resubscribe",
    ]
    assert "codex.sessions.shell" in wire_contract.params["all_jsonrpc_methods"]
    assert wire_contract.params["unsupported_method_error"]["code"] == -32601
    assert wire_contract.params["unsupported_method_error"]["data_fields"] == [
        "type",
        "method",
        "supported_methods",
        "protocol_version",
    ]
    assert any(
        "single-tenant, shared-workspace coding profile" in note
        for note in compatibility.params["consumer_guidance"]
    )
    assert any("urn:a2a:*" in note for note in compatibility.params["consumer_guidance"])
    shell_policy = compatibility.params["method_retention"]["codex.sessions.shell"]
    assert shell_policy["availability"] == "enabled"
    assert shell_policy["retention"] == "deployment-conditional"
    assert shell_policy["toggle"] == "A2A_ENABLE_SESSION_SHELL"


def test_agent_card_chat_examples_include_project_hint_when_configured() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token", a2a_project="alpha"))
    chat_skill = next(skill for skill in card.skills if skill.id == "codex.chat")
    assert any("project alpha" in example for example in chat_skill.examples)


def test_agent_card_omits_shell_method_when_disabled() -> None:
    card = build_agent_card(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_enable_session_shell=False,
            a2a_interrupt_request_ttl_seconds=45,
        )
    )
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}
    session_query = ext_by_uri[SESSION_QUERY_EXTENSION_URI]

    assert "shell" not in session_query.params["methods"]
    assert "shell" not in session_query.params["control_methods"]
    assert "codex.sessions.shell" not in session_query.params["method_contracts"]
    assert session_query.params["profile"]["runtime_features"]["session_shell"] == {
        "enabled": False,
        "availability": "disabled",
        "toggle": "A2A_ENABLE_SESSION_SHELL",
    }
    assert session_query.params["profile"]["runtime_features"]["interrupts"] == {
        "request_ttl_seconds": 45
    }
    wire_contract = ext_by_uri[WIRE_CONTRACT_EXTENSION_URI]
    assert "codex.sessions.shell" not in wire_contract.params["all_jsonrpc_methods"]
    assert wire_contract.params["extensions"]["conditionally_available_methods"] == {
        "codex.sessions.shell": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_SESSION_SHELL",
        }
    }
    compatibility = ext_by_uri[COMPATIBILITY_PROFILE_EXTENSION_URI]
    shell_policy = compatibility.params["method_retention"]["codex.sessions.shell"]
    assert shell_policy["availability"] == "disabled"
    assert compatibility.params["runtime_features"]["session_shell"] == {
        "enabled": False,
        "availability": "disabled",
        "toggle": "A2A_ENABLE_SESSION_SHELL",
    }
