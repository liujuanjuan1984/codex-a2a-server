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
        settings = Settings()
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
        "A2A_STREAM_SSE_PING_SECONDS": "8",
        "A2A_STREAM_IDLE_DIAGNOSTIC_SECONDS": "45",
        "A2A_INTERRUPT_REQUEST_TTL_SECONDS": "90",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings()
        assert settings.a2a_enable_health_endpoint is False
        assert settings.a2a_enable_session_shell is False
        assert settings.a2a_cancel_abort_timeout_seconds == 0.25
        assert settings.a2a_stream_sse_ping_seconds == 8
        assert settings.a2a_stream_idle_diagnostic_seconds == 45
        assert settings.a2a_interrupt_request_ttl_seconds == 90


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


def test_settings_reject_invalid_stream_ping_seconds():
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_STREAM_SSE_PING_SECONDS": "0",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
    assert "A2A_STREAM_SSE_PING_SECONDS" in str(excinfo.value)


def test_settings_reject_non_integer_stream_ping_seconds():
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_STREAM_SSE_PING_SECONDS": "8.5",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
    assert "A2A_STREAM_SSE_PING_SECONDS" in str(excinfo.value)


def test_settings_reject_invalid_stream_idle_diagnostic_seconds():
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_STREAM_IDLE_DIAGNOSTIC_SECONDS": "-1",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
    assert "A2A_STREAM_IDLE_DIAGNOSTIC_SECONDS" in str(excinfo.value)
