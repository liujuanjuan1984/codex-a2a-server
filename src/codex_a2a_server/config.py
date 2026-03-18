from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from codex_a2a_server import __version__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    # Codex settings (app-server mode)
    codex_base_url: str = Field(
        default="http://127.0.0.1:4096",
        alias="CODEX_BASE_URL",
    )
    codex_directory: str | None = Field(
        default=None,
        alias="CODEX_DIRECTORY",
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
    codex_system: str | None = Field(
        default=None,
        alias="CODEX_SYSTEM",
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
    a2a_interrupt_request_ttl_seconds: int = Field(
        default=3600,
        alias="A2A_INTERRUPT_REQUEST_TTL_SECONDS",
    )

    @field_validator("a2a_cancel_abort_timeout_seconds")
    @classmethod
    def validate_cancel_abort_timeout_seconds(cls, value: float) -> float:
        if value < 0:
            raise ValueError("A2A_CANCEL_ABORT_TIMEOUT_SECONDS must be >= 0")
        return value

    @field_validator("a2a_interrupt_request_ttl_seconds")
    @classmethod
    def validate_interrupt_request_ttl_seconds(cls, value: int) -> int:
        if value < 1:
            raise ValueError("A2A_INTERRUPT_REQUEST_TTL_SECONDS must be >= 1")
        return value
