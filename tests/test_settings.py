import os
from unittest import mock

import pytest
from pydantic import ValidationError

from codex_a2a_server import __version__
from codex_a2a_server.config import Settings


def test_settings_missing_required():
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings.from_env()
        # Should mention missing required fields
        errors = excinfo.value.errors()
        field_names = [e["loc"][0] for e in errors]
        assert "A2A_BEARER_TOKEN" in field_names


def test_settings_valid():
    env = {
        "A2A_BEARER_TOKEN": "test-token",
        "CODEX_TIMEOUT": "300",
        "CODEX_MODEL_REASONING_EFFORT": "high",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_bearer_token == "test-token"
        assert settings.codex_timeout == 300.0
        assert settings.codex_model_reasoning_effort == "high"
        assert settings.a2a_version == __version__


def test_parse_oauth_scopes():
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_OAUTH_SCOPES": "scope1, scope2,,scope3 ",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_oauth_scopes == {"scope1": "", "scope2": "", "scope3": ""}


def test_settings_parse_ops_flags_and_interrupt_ttl():
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_ENABLE_HEALTH_ENDPOINT": "false",
        "A2A_ENABLE_SESSION_SHELL": "false",
        "A2A_INTERRUPT_REQUEST_TTL_SECONDS": "90",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_enable_health_endpoint is False
        assert settings.a2a_enable_session_shell is False
        assert settings.a2a_interrupt_request_ttl_seconds == 90


def test_settings_reject_invalid_interrupt_request_ttl():
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_INTERRUPT_REQUEST_TTL_SECONDS": "0",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings.from_env()
    assert "A2A_INTERRUPT_REQUEST_TTL_SECONDS" in str(excinfo.value)
