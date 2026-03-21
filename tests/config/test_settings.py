import os
from unittest import mock

import pytest
from pydantic import ValidationError

from codex_a2a_server import __version__
from codex_a2a_server.config import Settings


def test_settings_missing_required():
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
        # Should mention missing required fields
        errors = excinfo.value.errors()
        field_names = [e["loc"][0] for e in errors]
        assert "A2A_BEARER_TOKEN" in field_names


def test_settings_valid():
    env = {
        "A2A_BEARER_TOKEN": "test-token",
        "CODEX_TIMEOUT": "300",
        "CODEX_MODEL_REASONING_EFFORT": "high",
        "CODEX_WORKSPACE_ROOT": "/tmp/workspace",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_bearer_token == "test-token"
        assert settings.codex_timeout == 300.0
        assert settings.codex_model_reasoning_effort == "high"
        assert settings.codex_workspace_root == "/tmp/workspace"
        assert settings.a2a_version == __version__


def test_settings_parse_ops_flags_and_timeouts():
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_ENABLE_HEALTH_ENDPOINT": "false",
        "A2A_ENABLE_SESSION_SHELL": "false",
        "A2A_CANCEL_ABORT_TIMEOUT_SECONDS": "0.25",
        "A2A_STREAM_IDLE_DIAGNOSTIC_SECONDS": "45",
        "A2A_INTERRUPT_REQUEST_TTL_SECONDS": "90",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_enable_health_endpoint is False
        assert settings.a2a_enable_session_shell is False
        assert settings.a2a_cancel_abort_timeout_seconds == 0.25
        assert settings.a2a_stream_idle_diagnostic_seconds == 45
        assert settings.a2a_interrupt_request_ttl_seconds == 90


def test_settings_parse_execution_environment_flags() -> None:
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_EXECUTION_SANDBOX_MODE": "workspace-write",
        "A2A_EXECUTION_SANDBOX_WRITABLE_ROOTS": "/workspace,/tmp/cache",
        "A2A_EXECUTION_NETWORK_ACCESS": "restricted",
        "A2A_EXECUTION_NETWORK_ALLOWED_DOMAINS": "api.openai.com,github.com",
        "A2A_EXECUTION_APPROVAL_POLICY": "on-request",
        "A2A_EXECUTION_APPROVAL_ESCALATION_BEHAVIOR": "per_request",
        "A2A_EXECUTION_WRITE_ACCESS_SCOPE": "configured_roots",
        "A2A_EXECUTION_WRITE_OUTSIDE_WORKSPACE": "true",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_execution_sandbox_mode == "workspace-write"
        assert settings.a2a_execution_sandbox_writable_roots == ["/workspace", "/tmp/cache"]
        assert settings.a2a_execution_network_access == "restricted"
        assert settings.a2a_execution_network_allowed_domains == [
            "api.openai.com",
            "github.com",
        ]
        assert settings.a2a_execution_approval_policy == "on-request"
        assert settings.a2a_execution_approval_escalation_behavior == "per_request"
        assert settings.a2a_execution_write_access_scope == "configured_roots"
        assert settings.a2a_execution_write_outside_workspace is True


def test_settings_reject_invalid_cancel_abort_timeout():
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_CANCEL_ABORT_TIMEOUT_SECONDS": "-0.1",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
    assert "A2A_CANCEL_ABORT_TIMEOUT_SECONDS" in str(excinfo.value)


def test_settings_reject_invalid_interrupt_request_ttl():
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_INTERRUPT_REQUEST_TTL_SECONDS": "0",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
    assert "A2A_INTERRUPT_REQUEST_TTL_SECONDS" in str(excinfo.value)


def test_settings_reject_invalid_stream_idle_diagnostic_seconds():
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_STREAM_IDLE_DIAGNOSTIC_SECONDS": "-1",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
    assert "A2A_STREAM_IDLE_DIAGNOSTIC_SECONDS" in str(excinfo.value)


def test_settings_reject_invalid_execution_sandbox_mode() -> None:
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_EXECUTION_SANDBOX_MODE": "sandboxed",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings.from_env()
    assert "A2A_EXECUTION_SANDBOX_MODE" in str(excinfo.value)
