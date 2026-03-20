from codex_a2a_server.profile import build_runtime_profile
from tests.helpers import make_settings


def test_runtime_profile_splits_stable_deployment_and_runtime_features() -> None:
    profile = build_runtime_profile(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_project="alpha",
            a2a_allow_directory_override=False,
            a2a_enable_session_shell=False,
            a2a_enable_health_endpoint=True,
            a2a_interrupt_request_ttl_seconds=90,
            codex_workspace_root="/srv/workspaces/alpha",
            codex_provider_id="google",
            codex_model_id="gemini-2.5-flash",
            codex_agent="code-reviewer",
            codex_variant="safe",
        )
    )

    assert profile.profile_id == "codex-a2a-single-tenant-coding-v1"
    assert profile.deployment.as_dict() == {
        "id": "single_tenant_shared_workspace",
        "single_tenant": True,
        "shared_workspace_across_consumers": True,
        "tenant_isolation": "none",
    }
    assert profile.runtime_features_dict() == {
        "directory_binding": {
            "allow_override": False,
            "scope": "workspace_root_only",
        },
        "session_shell": {
            "enabled": False,
            "availability": "disabled",
            "toggle": "A2A_ENABLE_SESSION_SHELL",
        },
        "interrupts": {
            "request_ttl_seconds": 90,
        },
        "service_features": {
            "streaming": {
                "enabled": True,
                "availability": "always",
            },
            "health_endpoint": {
                "enabled": True,
                "availability": "enabled",
                "toggle": "A2A_ENABLE_HEALTH_ENDPOINT",
            },
        },
    }
    assert profile.runtime_context.as_dict() == {
        "project": "alpha",
        "workspace_root": "/srv/workspaces/alpha",
        "provider_id": "google",
        "model_id": "gemini-2.5-flash",
        "agent": "code-reviewer",
        "variant": "safe",
    }
    assert profile.summary_dict()["runtime_context"]["workspace_root"] == "/srv/workspaces/alpha"
