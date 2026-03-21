from __future__ import annotations

from typing import Annotated, Any, cast

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from codex_a2a_server import __version__

_SANDBOX_MODES = {
    "unknown",
    "read-only",
    "workspace-write",
    "danger-full-access",
}
_FILESYSTEM_SCOPES = {
    "unknown",
    "none",
    "workspace_root",
    "workspace_root_or_descendant",
    "configured_roots",
    "full_filesystem",
}
_NETWORK_ACCESS_MODES = {
    "unknown",
    "disabled",
    "enabled",
    "restricted",
}
_APPROVAL_POLICIES = {
    "unknown",
    "never",
    "on-request",
    "on-failure",
    "untrusted-only",
}
_APPROVAL_ESCALATION_BEHAVIORS = {
    "unknown",
    "unavailable",
    "per_request",
    "fallback_only",
    "restricted",
}


def _parse_str_list(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        return [item.strip() for item in stripped.split(",") if item.strip()]
    if isinstance(value, tuple):
        return list(value)
    return value


def _validate_choice(value: str, *, allowed: set[str], env_name: str) -> str:
    if value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"{env_name} must be one of: {allowed_values}")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    # Codex settings (app-server mode)
    codex_workspace_root: str | None = Field(
        default=None,
        alias="CODEX_WORKSPACE_ROOT",
    )
    codex_provider_id: str | None = Field(
        default=None,
        alias="CODEX_PROVIDER_ID",
    )
    codex_model_id: str | None = Field(
        default=None,
        alias="CODEX_MODEL_ID",
    )
    codex_agent: str | None = Field(
        default=None,
        alias="CODEX_AGENT",
    )
    codex_variant: str | None = Field(
        default=None,
        alias="CODEX_VARIANT",
    )
    codex_timeout: float = Field(
        default=120.0,
        alias="CODEX_TIMEOUT",
    )
    codex_timeout_stream: float | None = Field(
        default=None,
        alias="CODEX_TIMEOUT_STREAM",
    )
    codex_cli_bin: str = Field(
        default="codex",
        alias="CODEX_CLI_BIN",
    )
    codex_app_server_listen: str = Field(
        default="stdio://",
        alias="CODEX_APP_SERVER_LISTEN",
    )
    codex_model: str = Field(
        default="gpt-5.1-codex",
        alias="CODEX_MODEL",
    )
    codex_model_reasoning_effort: str | None = Field(
        default=None,
        alias="CODEX_MODEL_REASONING_EFFORT",
    )

    # A2A settings
    a2a_public_url: str = Field(default="http://127.0.0.1:8000", alias="A2A_PUBLIC_URL")
    a2a_project: str | None = Field(default=None, alias="A2A_PROJECT")
    a2a_title: str = Field(default="Codex A2A", alias="A2A_TITLE")
    a2a_description: str = Field(default="A2A wrapper service for Codex", alias="A2A_DESCRIPTION")
    a2a_version: str = Field(default=__version__, alias="A2A_VERSION")
    a2a_protocol_version: str = Field(default="0.3.0", alias="A2A_PROTOCOL_VERSION")
    a2a_enable_health_endpoint: bool = Field(default=True, alias="A2A_ENABLE_HEALTH_ENDPOINT")
    a2a_enable_session_shell: bool = Field(default=True, alias="A2A_ENABLE_SESSION_SHELL")
    a2a_log_level: str = Field(default="INFO", alias="A2A_LOG_LEVEL")
    a2a_log_payloads: bool = Field(default=False, alias="A2A_LOG_PAYLOADS")
    a2a_log_body_limit: int = Field(default=0, alias="A2A_LOG_BODY_LIMIT")
    a2a_documentation_url: str | None = Field(default=None, alias="A2A_DOCUMENTATION_URL")
    a2a_allow_directory_override: bool = Field(default=True, alias="A2A_ALLOW_DIRECTORY_OVERRIDE")
    a2a_host: str = Field(default="127.0.0.1", alias="A2A_HOST")
    a2a_port: int = Field(default=8000, alias="A2A_PORT")
    a2a_bearer_token: str = Field(..., min_length=1, alias="A2A_BEARER_TOKEN")

    # Session cache settings
    a2a_session_cache_ttl_seconds: int = Field(default=3600, alias="A2A_SESSION_CACHE_TTL_SECONDS")
    a2a_session_cache_maxsize: int = Field(default=10_000, alias="A2A_SESSION_CACHE_MAXSIZE")
    a2a_cancel_abort_timeout_seconds: float = Field(
        default=1.0,
        alias="A2A_CANCEL_ABORT_TIMEOUT_SECONDS",
    )
    a2a_stream_idle_diagnostic_seconds: float = Field(
        default=60.0,
        alias="A2A_STREAM_IDLE_DIAGNOSTIC_SECONDS",
    )
    a2a_interrupt_request_ttl_seconds: int = Field(
        default=3600,
        alias="A2A_INTERRUPT_REQUEST_TTL_SECONDS",
    )
    a2a_execution_sandbox_mode: str = Field(
        default="unknown",
        alias="A2A_EXECUTION_SANDBOX_MODE",
    )
    a2a_execution_sandbox_filesystem_scope: str | None = Field(
        default=None,
        alias="A2A_EXECUTION_SANDBOX_FILESYSTEM_SCOPE",
    )
    a2a_execution_sandbox_writable_roots: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        alias="A2A_EXECUTION_SANDBOX_WRITABLE_ROOTS",
    )
    a2a_execution_network_access: str = Field(
        default="unknown",
        alias="A2A_EXECUTION_NETWORK_ACCESS",
    )
    a2a_execution_network_allowed_domains: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        alias="A2A_EXECUTION_NETWORK_ALLOWED_DOMAINS",
    )
    a2a_execution_approval_policy: str = Field(
        default="unknown",
        alias="A2A_EXECUTION_APPROVAL_POLICY",
    )
    a2a_execution_approval_escalation_behavior: str | None = Field(
        default=None,
        alias="A2A_EXECUTION_APPROVAL_ESCALATION_BEHAVIOR",
    )
    a2a_execution_write_access_scope: str | None = Field(
        default=None,
        alias="A2A_EXECUTION_WRITE_ACCESS_SCOPE",
    )
    a2a_execution_write_outside_workspace: bool | None = Field(
        default=None,
        alias="A2A_EXECUTION_WRITE_OUTSIDE_WORKSPACE",
    )

    @field_validator("a2a_cancel_abort_timeout_seconds")
    @classmethod
    def validate_cancel_abort_timeout_seconds(cls, value: float) -> float:
        if value < 0:
            raise ValueError("A2A_CANCEL_ABORT_TIMEOUT_SECONDS must be >= 0")
        return value

    @field_validator("a2a_stream_idle_diagnostic_seconds")
    @classmethod
    def validate_stream_idle_diagnostic_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("A2A_STREAM_IDLE_DIAGNOSTIC_SECONDS must be > 0")
        return value

    @field_validator("a2a_interrupt_request_ttl_seconds")
    @classmethod
    def validate_interrupt_request_ttl_seconds(cls, value: int) -> int:
        if value < 1:
            raise ValueError("A2A_INTERRUPT_REQUEST_TTL_SECONDS must be >= 1")
        return value

    @field_validator(
        "a2a_execution_sandbox_writable_roots",
        "a2a_execution_network_allowed_domains",
        mode="before",
    )
    @classmethod
    def parse_execution_lists(cls, value: Any) -> Any:
        return _parse_str_list(value)

    @field_validator("a2a_execution_sandbox_mode")
    @classmethod
    def validate_execution_sandbox_mode(cls, value: str) -> str:
        return _validate_choice(
            value,
            allowed=_SANDBOX_MODES,
            env_name="A2A_EXECUTION_SANDBOX_MODE",
        )

    @field_validator("a2a_execution_sandbox_filesystem_scope")
    @classmethod
    def validate_execution_sandbox_filesystem_scope(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_choice(
            value,
            allowed=_FILESYSTEM_SCOPES,
            env_name="A2A_EXECUTION_SANDBOX_FILESYSTEM_SCOPE",
        )

    @field_validator("a2a_execution_network_access")
    @classmethod
    def validate_execution_network_access(cls, value: str) -> str:
        return _validate_choice(
            value,
            allowed=_NETWORK_ACCESS_MODES,
            env_name="A2A_EXECUTION_NETWORK_ACCESS",
        )

    @field_validator("a2a_execution_approval_policy")
    @classmethod
    def validate_execution_approval_policy(cls, value: str) -> str:
        return _validate_choice(
            value,
            allowed=_APPROVAL_POLICIES,
            env_name="A2A_EXECUTION_APPROVAL_POLICY",
        )

    @field_validator("a2a_execution_approval_escalation_behavior")
    @classmethod
    def validate_execution_approval_escalation_behavior(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_choice(
            value,
            allowed=_APPROVAL_ESCALATION_BEHAVIORS,
            env_name="A2A_EXECUTION_APPROVAL_ESCALATION_BEHAVIOR",
        )

    @field_validator("a2a_execution_write_access_scope")
    @classmethod
    def validate_execution_write_access_scope(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_choice(
            value,
            allowed=_FILESYSTEM_SCOPES,
            env_name="A2A_EXECUTION_WRITE_ACCESS_SCOPE",
        )

    @classmethod
    def from_env(cls) -> Settings:
        settings_cls: type[BaseSettings] = cls
        return cast(Settings, settings_cls())
