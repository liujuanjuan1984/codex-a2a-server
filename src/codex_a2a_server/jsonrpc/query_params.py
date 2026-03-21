from __future__ import annotations

from typing import Any

from pydantic import ValidationError, field_validator

from codex_a2a_server.jsonrpc.params_common import (
    JsonRpcParamsValidationError,
    _PermissiveModel,
    format_loc,
    normalize_non_empty_string,
    normalize_session_query_limit,
    parse_positive_int,
)


class SessionQueryQueryParams(_PermissiveModel):
    limit: int | None = None
    cursor: Any | None = None
    page: Any | None = None
    size: Any | None = None

    @field_validator("limit", mode="before")
    @classmethod
    def _validate_limit(cls, value: Any) -> int | None:
        return parse_positive_int(value, field="limit")


class SessionListParams(_PermissiveModel):
    limit: int | None = None
    query: SessionQueryQueryParams | None = None
    cursor: Any | None = None
    page: Any | None = None
    size: Any | None = None

    @field_validator("limit", mode="before")
    @classmethod
    def _validate_limit(cls, value: Any) -> int | None:
        return parse_positive_int(value, field="limit")


class SessionMessagesParams(SessionListParams):
    session_id: str

    @field_validator("session_id", mode="before")
    @classmethod
    def _validate_session_id(cls, value: Any) -> str:
        return normalize_non_empty_string(value, message="Missing required params.session_id")


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
        data={"type": "INVALID_FIELD", "field": format_loc(loc)},
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
    return normalize_session_query_limit(query)


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
    return parsed.session_id, normalize_session_query_limit(query)
