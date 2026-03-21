from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from codex_a2a_server.jsonrpc.params_common import (
    JsonRpcParamsValidationError,
    MetadataParams,
    _PermissiveModel,
    _StrictModel,
    format_loc,
    map_extra_forbidden,
    normalize_non_empty_string,
    strip_optional_string,
)


class PromptTextPart(_PermissiveModel):
    type: Literal["text"]
    text: str

    @field_validator("type", mode="before")
    @classmethod
    def _validate_type(cls, value: Any) -> str:
        if value != "text":
            raise ValueError("Only text request parts are currently supported")
        return "text"

    @field_validator("text", mode="before")
    @classmethod
    def _validate_text(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("request.parts[].text must be a string")
        return value


class PromptAsyncRequestParams(_StrictModel):
    parts: list[PromptTextPart]
    message_id: str | None = Field(
        default=None,
        validation_alias="messageID",
        serialization_alias="messageID",
    )
    agent: str | None = None
    system: str | None = None
    variant: str | None = None

    @field_validator("parts", mode="before")
    @classmethod
    def _validate_parts(cls, value: Any) -> Any:
        if not isinstance(value, list) or not value:
            raise ValueError("request.parts must be a non-empty array")
        return value

    @field_validator("message_id", "agent", "system", "variant", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: Any) -> str | None:
        return strip_optional_string(value)


class CommandRequestParams(_StrictModel):
    command: str
    arguments: str | None = None
    message_id: str | None = Field(
        default=None,
        validation_alias="messageID",
        serialization_alias="messageID",
    )

    @field_validator("command", mode="before")
    @classmethod
    def _validate_command(cls, value: Any) -> str:
        return normalize_non_empty_string(
            value, message="request.command must be a non-empty string"
        )

    @field_validator("arguments", "message_id", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: Any) -> str | None:
        return strip_optional_string(value)


class ShellRequestParams(_StrictModel):
    command: str

    @field_validator("command", mode="before")
    @classmethod
    def _validate_command(cls, value: Any) -> str:
        return normalize_non_empty_string(
            value, message="request.command must be a non-empty string"
        )


class PromptAsyncControlParams(_StrictModel):
    session_id: str
    request: PromptAsyncRequestParams
    metadata: MetadataParams | None = None

    @field_validator("session_id", mode="before")
    @classmethod
    def _validate_session_id(cls, value: Any) -> str:
        return normalize_non_empty_string(value, message="Missing required params.session_id")


class CommandControlParams(_StrictModel):
    session_id: str
    request: CommandRequestParams
    metadata: MetadataParams | None = None

    @field_validator("session_id", mode="before")
    @classmethod
    def _validate_session_id(cls, value: Any) -> str:
        return normalize_non_empty_string(value, message="Missing required params.session_id")


class ShellControlParams(_StrictModel):
    session_id: str
    request: ShellRequestParams
    metadata: MetadataParams | None = None

    @field_validator("session_id", mode="before")
    @classmethod
    def _validate_session_id(cls, value: Any) -> str:
        return normalize_non_empty_string(value, message="Missing required params.session_id")


def _raise_control_validation_error(exc: ValidationError) -> None:
    errors = exc.errors(include_url=False)
    if errors and all(err.get("type") == "extra_forbidden" for err in errors):
        raise map_extra_forbidden(errors)

    first = errors[0]
    loc = tuple(first.get("loc", ()))
    if loc == ("session_id",):
        raise JsonRpcParamsValidationError(
            message="Missing required params.session_id",
            data={"type": "MISSING_FIELD", "field": "session_id"},
        )
    if loc == ("request",):
        raise JsonRpcParamsValidationError(
            message="params.request must be an object",
            data={"type": "INVALID_FIELD", "field": "request"},
        )
    if loc == ("request", "parts"):
        raise JsonRpcParamsValidationError(
            message="request.parts must be a non-empty array",
            data={"type": "INVALID_FIELD", "field": "request.parts"},
        )
    if loc == ("request", "command"):
        raise JsonRpcParamsValidationError(
            message="request.command must be a non-empty string",
            data={"type": "INVALID_FIELD", "field": "request.command"},
        )
    if loc == ("metadata",):
        raise JsonRpcParamsValidationError(
            message="metadata must be an object",
            data={"type": "INVALID_FIELD", "field": "metadata"},
        )
    if loc == ("metadata", "codex"):
        raise JsonRpcParamsValidationError(
            message="metadata.codex must be an object",
            data={"type": "INVALID_FIELD", "field": "metadata.codex"},
        )
    if loc == ("metadata", "codex", "directory"):
        raise JsonRpcParamsValidationError(
            message="metadata.codex.directory must be a string",
            data={"type": "INVALID_FIELD", "field": "metadata.codex.directory"},
        )
    if loc in {
        ("request", "arguments"),
        ("request", "messageID"),
        ("request", "agent"),
        ("request", "system"),
        ("request", "variant"),
    }:
        field = format_loc(loc)
        raise JsonRpcParamsValidationError(
            message=f"{field} must be a string",
            data={"type": "INVALID_FIELD", "field": field},
        )
    if len(loc) >= 3 and loc[:2] == ("request", "parts") and loc[-1] == "text":
        field = format_loc(loc)
        raise JsonRpcParamsValidationError(
            message=f"{field} must be a string",
            data={"type": "INVALID_FIELD", "field": field},
        )
    if len(loc) >= 2 and loc[:2] == ("request", "parts") and loc[-1] != "type":
        field = format_loc(loc)
        raise JsonRpcParamsValidationError(
            message=f"{field} must be an object",
            data={"type": "INVALID_FIELD", "field": field},
        )
    if loc:
        field = format_loc(loc)
        raise JsonRpcParamsValidationError(
            message=str(first.get("msg", "Invalid params")).removeprefix("Value error, "),
            data={"type": "INVALID_FIELD", "field": field},
        )
    raise JsonRpcParamsValidationError(
        message=str(first.get("msg", "Invalid params")),
        data={"type": "INVALID_FIELD"},
    )


def parse_prompt_async_params(params: dict[str, Any]) -> PromptAsyncControlParams:
    try:
        return PromptAsyncControlParams.model_validate(params)
    except ValidationError as exc:
        _raise_control_validation_error(exc)
        raise AssertionError("unreachable") from exc


def parse_command_params(params: dict[str, Any]) -> CommandControlParams:
    try:
        return CommandControlParams.model_validate(params)
    except ValidationError as exc:
        _raise_control_validation_error(exc)
        raise AssertionError("unreachable") from exc


def parse_shell_params(params: dict[str, Any]) -> ShellControlParams:
    try:
        return ShellControlParams.model_validate(params)
    except ValidationError as exc:
        _raise_control_validation_error(exc)
        raise AssertionError("unreachable") from exc
