import pytest

from codex_a2a_server.contracts.extensions import (
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
)
from codex_a2a_server.jsonrpc.params import (
    JsonRpcParamsValidationError,
    parse_get_session_messages_params,
    parse_list_sessions_params,
    parse_permission_reply_params,
    parse_prompt_async_params,
)


def test_parse_prompt_async_params_preserves_aliases() -> None:
    payload = parse_prompt_async_params(
        {
            "session_id": "s-1",
            "request": {
                "parts": [{"type": "text", "text": "hello"}],
                "messageID": "msg-1",
            },
            "metadata": {"codex": {"directory": "/workspace"}},
        }
    )

    assert payload.session_id == "s-1"
    assert payload.request.model_dump(by_alias=True, exclude_none=True) == {
        "parts": [{"type": "text", "text": "hello"}],
        "messageID": "msg-1",
    }
    assert payload.metadata is not None
    assert payload.metadata.codex is not None
    assert payload.metadata.codex.directory == "/workspace"


@pytest.mark.parametrize(
    ("payload", "message", "data"),
    [
        (
            {
                "session_id": "s-1",
                "request": {
                    "parts": [{"type": "text", "text": "hello"}],
                    "extra": True,
                },
            },
            "Unsupported fields: request.extra",
            {
                "type": "INVALID_FIELD",
                "field": "request",
                "fields": ["request.extra"],
            },
        ),
        (
            {
                "session_id": "s-1",
                "request": {"parts": [{"type": "text", "text": "hello"}]},
                "metadata": {"extra": True},
            },
            "Unsupported metadata fields: extra",
            {
                "type": "INVALID_FIELD",
                "fields": ["metadata.extra"],
            },
        ),
    ],
)
def test_parse_prompt_async_params_rejects_unknown_fields(
    payload: dict,
    message: str,
    data: dict,
) -> None:
    with pytest.raises(JsonRpcParamsValidationError) as exc_info:
        parse_prompt_async_params(payload)

    assert str(exc_info.value) == message
    assert exc_info.value.data == data


def test_parse_permission_reply_params_rejects_missing_reply() -> None:
    with pytest.raises(JsonRpcParamsValidationError) as exc_info:
        parse_permission_reply_params({"request_id": "perm-1"})

    assert str(exc_info.value) == "reply must be a string"
    assert exc_info.value.data == {"type": "INVALID_FIELD", "field": "reply"}
    assert "fields" not in exc_info.value.data


def test_parse_list_sessions_params_rejects_non_integer_limit() -> None:
    with pytest.raises(JsonRpcParamsValidationError) as exc_info:
        parse_list_sessions_params({"limit": "abc"})

    assert str(exc_info.value) == "limit must be an integer"
    assert exc_info.value.data == {"type": "INVALID_FIELD", "field": "limit"}
    assert "fields" not in exc_info.value.data


def test_parse_list_sessions_params_applies_default_limit() -> None:
    query = parse_list_sessions_params({})

    assert query == {"limit": SESSION_QUERY_DEFAULT_LIMIT}


def test_parse_list_sessions_params_rejects_limit_above_max() -> None:
    with pytest.raises(JsonRpcParamsValidationError) as exc_info:
        parse_list_sessions_params({"limit": SESSION_QUERY_MAX_LIMIT + 1})

    assert str(exc_info.value) == f"limit must be <= {SESSION_QUERY_MAX_LIMIT}"
    assert exc_info.value.data == {"type": "INVALID_FIELD", "field": "limit"}


def test_parse_get_session_messages_params_returns_session_and_query() -> None:
    session_id, query = parse_get_session_messages_params(
        {
            "session_id": "s-1",
            "limit": "3",
            "query": {"cursor": None, "tag": "ops"},
        }
    )

    assert session_id == "s-1"
    assert query == {"tag": "ops", "limit": 3}


def test_parse_get_session_messages_params_applies_default_limit() -> None:
    session_id, query = parse_get_session_messages_params({"session_id": "s-1"})

    assert session_id == "s-1"
    assert query == {"limit": SESSION_QUERY_DEFAULT_LIMIT}


def test_parse_prompt_async_params_only_uses_fields_for_unsupported_fields() -> None:
    with pytest.raises(JsonRpcParamsValidationError) as unsupported_exc:
        parse_prompt_async_params(
            {
                "session_id": "s-1",
                "request": {
                    "parts": [{"type": "text", "text": "hello"}],
                    "extra": True,
                },
            }
        )

    assert unsupported_exc.value.data["field"] == "request"
    assert unsupported_exc.value.data["fields"] == ["request.extra"]

    with pytest.raises(JsonRpcParamsValidationError) as invalid_type_exc:
        parse_prompt_async_params(
            {
                "session_id": "s-1",
                "request": {"parts": [{"type": "text", "text": 1}]},
            }
        )

    assert invalid_type_exc.value.data == {
        "type": "INVALID_FIELD",
        "field": "request.parts[0].text",
    }
    assert "fields" not in invalid_type_exc.value.data
