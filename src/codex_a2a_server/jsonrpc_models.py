from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from a2a._base import A2ABaseModel
from pydantic import ConfigDict, Field, ValidationError, field_validator

from .extension_contracts import SESSION_QUERY_DEFAULT_LIMIT, SESSION_QUERY_MAX_LIMIT


class JsonRpcParamsValidationError(ValueError):
    def __init__(self, *, message: str, data: dict[str, Any]) -> None:
        super().__init__(message)
        self.data = data


class _StrictModel(A2ABaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _PermissiveModel(A2ABaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


def _strip_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be a string")
    return value


def _normalize_non_empty_string(value: Any, *, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(message)
    return value.strip()


def _parse_positive_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an integer") from exc
    else:
        raise ValueError(f"{field} must be an integer")
    if parsed < 1:
        raise ValueError(f"{field} must be >= 1")
    return parsed


def _format_loc(parts: tuple[Any, ...]) -> str:
    rendered: list[str] = []
    for part in parts:
        if isinstance(part, int):
            if rendered:
                rendered[-1] = f"{rendered[-1]}[{part}]"
            else:
                rendered.append(f"[{part}]")
            continue
        rendered.append(str(part))
    return ".".join(rendered)


def _normalize_session_query_limit(query: dict[str, Any]) -> dict[str, Any]:
    limit = query.get("limit")
    if limit is None:
        query["limit"] = SESSION_QUERY_DEFAULT_LIMIT
        return query

    normalized_limit = int(limit)
    if normalized_limit > SESSION_QUERY_MAX_LIMIT:
        raise JsonRpcParamsValidationError(
            message=f"limit must be <= {SESSION_QUERY_MAX_LIMIT}",
            data={"type": "INVALID_FIELD", "field": "limit"},
        )

    query["limit"] = normalized_limit
    return query


def _map_extra_forbidden(errors: Sequence[Mapping[str, Any]]) -> JsonRpcParamsValidationError:
    fields = sorted({_format_loc(tuple(err.get("loc", ()))) for err in errors})
    if fields and all(field.startswith("request.") for field in fields):
        return JsonRpcParamsValidationError(
            message=f"Unsupported fields: {', '.join(fields)}",
            data={
                "type": "INVALID_FIELD",
                "field": "request",
                "fields": fields,
            },
        )
    if fields and all(field.startswith("metadata.") for field in fields):
        metadata_fields = ", ".join(field.removeprefix("metadata.") for field in fields)
        return JsonRpcParamsValidationError(
            message=f"Unsupported metadata fields: {metadata_fields}",
            data={
                "type": "INVALID_FIELD",
                "fields": fields,
            },
        )
    return JsonRpcParamsValidationError(
        message=f"Unsupported fields: {', '.join(fields)}",
        data={
            "type": "INVALID_FIELD",
            "fields": fields,
        },
    )


class SessionQueryQueryParams(_PermissiveModel):
    limit: int | None = None
    cursor: Any | None = None
    page: Any | None = None
    size: Any | None = None

    @field_validator("limit", mode="before")
    @classmethod
    def _validate_limit(cls, value: Any) -> int | None:
        return _parse_positive_int(value, field="limit")


class SessionListParams(_PermissiveModel):
    limit: int | None = None
    query: SessionQueryQueryParams | None = None
    cursor: Any | None = None
    page: Any | None = None
    size: Any | None = None

    @field_validator("limit", mode="before")
    @classmethod
    def _validate_limit(cls, value: Any) -> int | None:
        return _parse_positive_int(value, field="limit")


class SessionMessagesParams(SessionListParams):
    session_id: str

    @field_validator("session_id", mode="before")
    @classmethod
    def _validate_session_id(cls, value: Any) -> str:
        return _normalize_non_empty_string(value, message="Missing required params.session_id")


class CodexMetadataParams(_PermissiveModel):
    directory: str | None = None

    @field_validator("directory", mode="before")
    @classmethod
    def _validate_directory(cls, value: Any) -> str | None:
        return _strip_optional_string(value)


class MetadataParams(_StrictModel):
    codex: CodexMetadataParams | None = None


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
        return _strip_optional_string(value)


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
        return _normalize_non_empty_string(
            value, message="request.command must be a non-empty string"
        )

    @field_validator("arguments", "message_id", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: Any) -> str | None:
        return _strip_optional_string(value)


class ShellRequestParams(_StrictModel):
    command: str

    @field_validator("command", mode="before")
    @classmethod
    def _validate_command(cls, value: Any) -> str:
        return _normalize_non_empty_string(
            value, message="request.command must be a non-empty string"
        )


class PromptAsyncControlParams(_StrictModel):
    session_id: str
    request: PromptAsyncRequestParams
    metadata: MetadataParams | None = None

    @field_validator("session_id", mode="before")
    @classmethod
    def _validate_session_id(cls, value: Any) -> str:
        return _normalize_non_empty_string(value, message="Missing required params.session_id")


class CommandControlParams(_StrictModel):
    session_id: str
    request: CommandRequestParams
    metadata: MetadataParams | None = None

    @field_validator("session_id", mode="before")
    @classmethod
    def _validate_session_id(cls, value: Any) -> str:
        return _normalize_non_empty_string(value, message="Missing required params.session_id")


class ShellControlParams(_StrictModel):
    session_id: str
    request: ShellRequestParams
    metadata: MetadataParams | None = None

    @field_validator("session_id", mode="before")
    @classmethod
    def _validate_session_id(cls, value: Any) -> str:
        return _normalize_non_empty_string(value, message="Missing required params.session_id")


class PermissionReplyParams(_StrictModel):
    request_id: str
    reply: Literal["once", "always", "reject"]
    message: str | None = None
    metadata: MetadataParams | None = None

    @field_validator("request_id", mode="before")
    @classmethod
    def _validate_request_id(cls, value: Any) -> str:
        return _normalize_non_empty_string(value, message="Missing required params.request_id")

    @field_validator("reply", mode="before")
    @classmethod
    def _validate_reply(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("reply must be a string")
        normalized = value.strip().lower()
        if normalized not in {"once", "always", "reject"}:
            raise ValueError("reply must be one of: once, always, reject")
        return normalized

    @field_validator("message", mode="before")
    @classmethod
    def _validate_message(cls, value: Any) -> str | None:
        return _strip_optional_string(value)


class QuestionReplyParams(_StrictModel):
    request_id: str
    answers: list[list[str]]
    metadata: MetadataParams | None = None

    @field_validator("request_id", mode="before")
    @classmethod
    def _validate_request_id(cls, value: Any) -> str:
        return _normalize_non_empty_string(value, message="Missing required params.request_id")

    @field_validator("answers", mode="before")
    @classmethod
    def _validate_answers(cls, value: Any) -> list[list[str]]:
        if not isinstance(value, list):
            raise ValueError("answers must be an array")
        answers: list[list[str]] = []
        for index, item in enumerate(value):
            if not isinstance(item, list):
                raise ValueError(f"answers[{index}] must be an array of strings")
            parsed_group: list[str] = []
            for option in item:
                if not isinstance(option, str):
                    raise ValueError(f"answers[{index}] must contain only strings")
                normalized = option.strip()
                if normalized:
                    parsed_group.append(normalized)
            answers.append(parsed_group)
        return answers


class QuestionRejectParams(_StrictModel):
    request_id: str
    metadata: MetadataParams | None = None

    @field_validator("request_id", mode="before")
    @classmethod
    def _validate_request_id(cls, value: Any) -> str:
        return _normalize_non_empty_string(value, message="Missing required params.request_id")


def _raise_query_validation_error(exc: ValidationError) -> None:
    first = exc.errors(include_url=False)[0]
    loc = tuple(first.get("loc", ()))
    if loc == ("query",):
        raise JsonRpcParamsValidationError(
            message="query must be an object",
            data={"type": "INVALID_FIELD", "field": "query"},
        )
    if loc == ("session_id",):
        raise JsonRpcParamsValidationError(
            message="Missing required params.session_id",
            data={"type": "MISSING_FIELD", "field": "session_id"},
        )
    if loc in {("limit",), ("query", "limit")}:
        message = str(first.get("msg", "limit must be an integer")).removeprefix("Value error, ")
        raise JsonRpcParamsValidationError(
            message=message,
            data={"type": "INVALID_FIELD", "field": "limit"},
        )
    raise JsonRpcParamsValidationError(
        message=str(first.get("msg", "Invalid params")),
        data={"type": "INVALID_FIELD", "field": _format_loc(loc)},
    )


def _raise_control_validation_error(exc: ValidationError) -> None:
    errors = exc.errors(include_url=False)
    if errors and all(err.get("type") == "extra_forbidden" for err in errors):
        raise _map_extra_forbidden(errors)

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
        field = _format_loc(loc)
        raise JsonRpcParamsValidationError(
            message=f"{field} must be a string",
            data={"type": "INVALID_FIELD", "field": field},
        )
    if len(loc) >= 3 and loc[:2] == ("request", "parts") and loc[-1] == "text":
        field = _format_loc(loc)
        raise JsonRpcParamsValidationError(
            message=f"{field} must be a string",
            data={"type": "INVALID_FIELD", "field": field},
        )
    if len(loc) >= 2 and loc[:2] == ("request", "parts") and loc[-1] != "type":
        field = _format_loc(loc)
        raise JsonRpcParamsValidationError(
            message=f"{field} must be an object",
            data={"type": "INVALID_FIELD", "field": field},
        )
    if loc:
        field = _format_loc(loc)
        raise JsonRpcParamsValidationError(
            message=str(first.get("msg", "Invalid params")).removeprefix("Value error, "),
            data={"type": "INVALID_FIELD", "field": field},
        )
    raise JsonRpcParamsValidationError(
        message=str(first.get("msg", "Invalid params")),
        data={"type": "INVALID_FIELD"},
    )


def _raise_interrupt_validation_error(exc: ValidationError) -> None:
    errors = exc.errors(include_url=False)
    if errors and all(err.get("type") == "extra_forbidden" for err in errors):
        raise _map_extra_forbidden(errors)

    first = errors[0]
    loc = tuple(first.get("loc", ()))
    if loc == ("request_id",):
        raise JsonRpcParamsValidationError(
            message="Missing required params.request_id",
            data={"type": "MISSING_FIELD", "field": "request_id"},
        )
    if loc == ("reply",):
        message = str(first.get("msg", "reply must be a string")).removeprefix("Value error, ")
        if first.get("type") == "missing":
            message = "reply must be a string"
        raise JsonRpcParamsValidationError(
            message=message,
            data={"type": "INVALID_FIELD", "field": "reply"},
        )
    if loc == ("message",):
        raise JsonRpcParamsValidationError(
            message="message must be a string",
            data={"type": "INVALID_FIELD", "field": "message"},
        )
    if loc == ("answers",):
        message = str(first.get("msg", "answers must be an array")).removeprefix("Value error, ")
        if first.get("type") == "missing":
            message = "answers must be an array"
        raise JsonRpcParamsValidationError(
            message=message,
            data={"type": "INVALID_FIELD", "field": "answers"},
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
    if loc:
        raise JsonRpcParamsValidationError(
            message=str(first.get("msg", "Invalid params")).removeprefix("Value error, "),
            data={"type": "INVALID_FIELD", "field": _format_loc(loc)},
        )
    raise JsonRpcParamsValidationError(
        message=str(first.get("msg", "Invalid params")),
        data={"type": "INVALID_FIELD"},
    )


def parse_list_sessions_params(params: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = SessionListParams.model_validate(params)
    except ValidationError as exc:
        _raise_query_validation_error(exc)

    query_model = parsed.query
    if parsed.cursor is not None or parsed.page is not None or parsed.size is not None:
        raise JsonRpcParamsValidationError(
            message="Only limit pagination is supported",
            data={
                "type": "INVALID_PAGINATION_MODE",
                "supported": ["limit"],
                "unsupported": ["cursor", "page", "size"],
            },
        )
    if query_model and (
        query_model.cursor is not None
        or query_model.page is not None
        or query_model.size is not None
    ):
        raise JsonRpcParamsValidationError(
            message="Only limit pagination is supported",
            data={
                "type": "INVALID_PAGINATION_MODE",
                "supported": ["limit"],
                "unsupported": ["cursor", "page", "size"],
            },
        )
    if parsed.limit is not None and query_model and query_model.limit is not None:
        if parsed.limit != query_model.limit:
            raise JsonRpcParamsValidationError(
                message="limit is ambiguous between params.limit and params.query.limit",
                data={"type": "INVALID_FIELD", "field": "limit"},
            )

    query: dict[str, Any] = {}
    if query_model is not None:
        query.update(query_model.model_dump(exclude_none=True))
        query.update(query_model.model_extra or {})
        query.pop("cursor", None)
        query.pop("page", None)
        query.pop("size", None)
    if parsed.limit is not None:
        query["limit"] = parsed.limit
    return _normalize_session_query_limit(query)


def parse_get_session_messages_params(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    try:
        parsed = SessionMessagesParams.model_validate(params)
    except ValidationError as exc:
        _raise_query_validation_error(exc)
    query_model = parsed.query
    if parsed.cursor is not None or parsed.page is not None or parsed.size is not None:
        raise JsonRpcParamsValidationError(
            message="Only limit pagination is supported",
            data={
                "type": "INVALID_PAGINATION_MODE",
                "supported": ["limit"],
                "unsupported": ["cursor", "page", "size"],
            },
        )
    if query_model and (
        query_model.cursor is not None
        or query_model.page is not None
        or query_model.size is not None
    ):
        raise JsonRpcParamsValidationError(
            message="Only limit pagination is supported",
            data={
                "type": "INVALID_PAGINATION_MODE",
                "supported": ["limit"],
                "unsupported": ["cursor", "page", "size"],
            },
        )
    if parsed.limit is not None and query_model and query_model.limit is not None:
        if parsed.limit != query_model.limit:
            raise JsonRpcParamsValidationError(
                message="limit is ambiguous between params.limit and params.query.limit",
                data={"type": "INVALID_FIELD", "field": "limit"},
            )

    query: dict[str, Any] = {}
    if query_model is not None:
        query.update(query_model.model_dump(exclude_none=True))
        query.update(query_model.model_extra or {})
        query.pop("cursor", None)
        query.pop("page", None)
        query.pop("size", None)
    if parsed.limit is not None:
        query["limit"] = parsed.limit
    return parsed.session_id, _normalize_session_query_limit(query)


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


def parse_permission_reply_params(params: dict[str, Any]) -> PermissionReplyParams:
    try:
        return PermissionReplyParams.model_validate(params)
    except ValidationError as exc:
        _raise_interrupt_validation_error(exc)
        raise AssertionError("unreachable") from exc


def parse_question_reply_params(params: dict[str, Any]) -> QuestionReplyParams:
    try:
        return QuestionReplyParams.model_validate(params)
    except ValidationError as exc:
        _raise_interrupt_validation_error(exc)
        raise AssertionError("unreachable") from exc


def parse_question_reject_params(params: dict[str, Any]) -> QuestionRejectParams:
    try:
        return QuestionRejectParams.model_validate(params)
    except ValidationError as exc:
        _raise_interrupt_validation_error(exc)
        raise AssertionError("unreachable") from exc


__all__ = [
    "CommandControlParams",
    "JsonRpcParamsValidationError",
    "MetadataParams",
    "PermissionReplyParams",
    "PromptAsyncControlParams",
    "QuestionRejectParams",
    "QuestionReplyParams",
    "ShellControlParams",
    "parse_command_params",
    "parse_get_session_messages_params",
    "parse_list_sessions_params",
    "parse_permission_reply_params",
    "parse_prompt_async_params",
    "parse_question_reject_params",
    "parse_question_reply_params",
    "parse_shell_params",
]
