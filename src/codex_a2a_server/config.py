from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

STREAM_HEARTBEAT_MIN_SECONDS = 5.0
STREAM_HEARTBEAT_RECOMMENDED_MIN_SECONDS = 10.0
STREAM_HEARTBEAT_RECOMMENDED_MAX_SECONDS = 15.0
STREAM_HEARTBEAT_MAX_SECONDS = 60.0


def stream_heartbeat_warning_message(heartbeat_seconds: float | None) -> str | None:
    if heartbeat_seconds is None:
        return None
    if (
        STREAM_HEARTBEAT_RECOMMENDED_MIN_SECONDS
        <= heartbeat_seconds
        <= STREAM_HEARTBEAT_RECOMMENDED_MAX_SECONDS
    ):
        return None
    return (
        "A2A_STREAM_HEARTBEAT_SECONDS="
        f"{heartbeat_seconds} is outside the recommended range "
        f"{STREAM_HEARTBEAT_RECOMMENDED_MIN_SECONDS:.0f}-"
        f"{STREAM_HEARTBEAT_RECOMMENDED_MAX_SECONDS:.0f} seconds; "
        "the service will keep the configured value"
    )


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
    a2a_version: str = Field(default="0.1.0", alias="A2A_VERSION")
    a2a_protocol_version: str = Field(default="0.3.0", alias="A2A_PROTOCOL_VERSION")
    a2a_streaming: bool = Field(default=True, alias="A2A_STREAMING")
    a2a_stream_heartbeat_seconds: float | None = Field(
        default=None,
        alias="A2A_STREAM_HEARTBEAT_SECONDS",
        description=(
            "Optional idle heartbeat threshold in seconds for client-visible A2A stream "
            "status updates. Disabled when unset. Recommended range: 10-15 seconds. "
            "Values below 5 or above 60 are rejected."
        ),
    )
    a2a_log_level: str = Field(default="INFO", alias="A2A_LOG_LEVEL")
    a2a_log_payloads: bool = Field(default=False, alias="A2A_LOG_PAYLOADS")
    a2a_log_body_limit: int = Field(default=0, alias="A2A_LOG_BODY_LIMIT")
    a2a_documentation_url: str | None = Field(default=None, alias="A2A_DOCUMENTATION_URL")
    a2a_allow_directory_override: bool = Field(default=True, alias="A2A_ALLOW_DIRECTORY_OVERRIDE")
    a2a_host: str = Field(default="127.0.0.1", alias="A2A_HOST")
    a2a_port: int = Field(default=8000, alias="A2A_PORT")
    a2a_bearer_token: str = Field(..., min_length=1, alias="A2A_BEARER_TOKEN")

    # OAuth2 settings
    a2a_oauth_authorization_url: str | None = Field(
        default=None, alias="A2A_OAUTH_AUTHORIZATION_URL"
    )
    a2a_oauth_token_url: str | None = Field(default=None, alias="A2A_OAUTH_TOKEN_URL")
    a2a_oauth_metadata_url: str | None = Field(default=None, alias="A2A_OAUTH_METADATA_URL")
    a2a_oauth_scopes: Any = Field(default_factory=dict, alias="A2A_OAUTH_SCOPES")

    # Session cache settings
    a2a_session_cache_ttl_seconds: int = Field(default=3600, alias="A2A_SESSION_CACHE_TTL_SECONDS")
    a2a_session_cache_maxsize: int = Field(default=10_000, alias="A2A_SESSION_CACHE_MAXSIZE")

    @field_validator("a2a_oauth_scopes", mode="before")
    @classmethod
    def parse_oauth_scopes(cls, v: Any) -> dict[str, str]:
        if isinstance(v, dict):
            return v
        if not isinstance(v, str) or not v:
            return {}
        scopes: dict[str, str] = {}
        for raw in v.split(","):
            scope = raw.strip()
            if scope:
                scopes[scope] = ""
        return scopes

    @field_validator("a2a_stream_heartbeat_seconds")
    @classmethod
    def validate_stream_heartbeat_seconds(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if v < STREAM_HEARTBEAT_MIN_SECONDS:
            raise ValueError(
                "A2A_STREAM_HEARTBEAT_SECONDS must be at least "
                f"{STREAM_HEARTBEAT_MIN_SECONDS:g} seconds"
            )
        if v > STREAM_HEARTBEAT_MAX_SECONDS:
            raise ValueError(
                "A2A_STREAM_HEARTBEAT_SECONDS must be at most "
                f"{STREAM_HEARTBEAT_MAX_SECONDS:g} seconds"
            )
        return v

    @classmethod
    def from_env(cls) -> Settings:
        # Pydantic BaseSettings automatically loads from environment
        return cls()
