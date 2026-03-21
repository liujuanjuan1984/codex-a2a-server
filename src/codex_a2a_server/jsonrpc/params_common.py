from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from a2a._base import A2ABaseModel
from pydantic import ConfigDict, field_validator

from codex_a2a_server.contracts.extensions import (
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
)


class JsonRpcParamsValidationError(ValueError):
    def __init__(self, *, message: str, data: dict[str, Any]) -> None:
        super().__init__(message)
        self.data = data


class _StrictModel(A2ABaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _PermissiveModel(A2ABaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


def strip_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be a string")
    return value


def normalize_non_empty_string(value: Any, *, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(message)
    return value.strip()


def parse_positive_int(value: Any, *, field: str) -> int | None:
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


def format_loc(parts: tuple[Any, ...]) -> str:
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


def normalize_session_query_limit(query: dict[str, Any]) -> dict[str, Any]:
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


def map_extra_forbidden(errors: Sequence[Mapping[str, Any]]) -> JsonRpcParamsValidationError:
    fields = sorted({format_loc(tuple(err.get("loc", ()))) for err in errors})
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


class CodexMetadataParams(_PermissiveModel):
    directory: str | None = None

    @field_validator("directory", mode="before")
    @classmethod
    def _validate_directory(cls, value: Any) -> str | None:
        return strip_optional_string(value)


class MetadataParams(_StrictModel):
    codex: CodexMetadataParams | None = None
