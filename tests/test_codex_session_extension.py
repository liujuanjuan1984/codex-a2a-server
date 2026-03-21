import logging
from unittest.mock import MagicMock

import httpx
import pytest

from codex_a2a_server.app import build_agent_card
from codex_a2a_server.config import Settings
from codex_a2a_server.extension_contracts import (
    INTERRUPT_CALLBACK_METHODS,
    SESSION_CONTROL_METHODS,
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
    SESSION_QUERY_METHODS,
    build_supported_jsonrpc_methods,
)
from codex_a2a_server.jsonrpc_ext import CodexSessionQueryJSONRPCApplication
from codex_a2a_server.profile import build_runtime_profile
from tests.helpers import DummySessionQueryCodexClient as DummyCodexClient
from tests.helpers import make_settings

_BASE_SETTINGS = {
    "codex_timeout": 1.0,
    "a2a_log_level": "DEBUG",
}


def _build_extension_app(
    *,
    session_claim=None,
    session_claim_finalize=None,
    session_claim_release=None,
    session_owner_matcher=None,
) -> CodexSessionQueryJSONRPCApplication:
    settings = make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    methods = {
        **SESSION_QUERY_METHODS,
        **SESSION_CONTROL_METHODS,
        **INTERRUPT_CALLBACK_METHODS,
    }
    return CodexSessionQueryJSONRPCApplication(
        agent_card=build_agent_card(settings),
        http_handler=MagicMock(),
        codex_client=DummyCodexClient(settings),
        methods=methods,
        protocol_version=settings.a2a_protocol_version,
        supported_methods=build_supported_jsonrpc_methods(
            runtime_profile=build_runtime_profile(settings)
        ),
        session_claim=session_claim,
        session_claim_finalize=session_claim_finalize,
        session_claim_release=session_claim_release,
        session_owner_matcher=session_owner_matcher,
    )


def test_session_extension_fails_fast_when_session_control_hooks_are_missing() -> None:
    async def owner_matcher(*, identity: str, session_id: str) -> bool:
        del identity, session_id
        return True

    with pytest.raises(ValueError, match="missing required session control hooks"):
        _build_extension_app(session_owner_matcher=owner_matcher)


def test_session_extension_fails_fast_when_interrupt_owner_hook_is_missing() -> None:
    async def claim(*, identity: str, session_id: str) -> bool:
        del identity, session_id
        return False

    async def finalize(*, identity: str, session_id: str) -> None:
        del identity, session_id

    async def release(*, identity: str, session_id: str) -> None:
        del identity, session_id

    with pytest.raises(ValueError, match="missing required interrupt ownership hook"):
        _build_extension_app(
            session_claim=claim,
            session_claim_finalize=finalize,
            session_claim_release=release,
        )


@pytest.mark.asyncio
async def test_session_query_extension_requires_bearer_token(monkeypatch):
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyCodexClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 1, "method": "codex.sessions.list", "params": {}},
        )
        assert resp.status_code == 401

        resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "codex.sessions.messages.list",
                "params": {"session_id": "s-1"},
            },
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_session_query_extension_returns_jsonrpc_result(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "codex.sessions.list",
                "params": {"limit": 10},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
        assert "raw" not in payload["result"]
        session = payload["result"]["items"][0]
        assert session["id"] == "s-1"
        assert session["contextId"] == "s-1"
        assert session["contextId"] == session["metadata"]["shared"]["session"]["id"]
        assert session["metadata"]["shared"]["session"]["id"] == "s-1"
        assert session["metadata"]["shared"]["session"]["title"] == "Session s-1"
        assert session["metadata"]["codex"]["raw"]["id"] == "s-1"
        assert dummy.last_sessions_params is not None
        assert dummy.last_sessions_params.get("limit") == 10

        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "codex.sessions.messages.list",
                "params": {"session_id": "s-1", "limit": 5},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 2
        assert "raw" not in payload["result"]
        message = payload["result"]["items"][0]
        assert message["contextId"] == "s-1"
        assert message["contextId"] == message["metadata"]["shared"]["session"]["id"]
        assert message["parts"][0]["text"] == "SECRET_HISTORY"
        assert message["metadata"]["shared"]["session"]["id"] == "s-1"
        assert dummy.last_messages_params is not None
        assert dummy.last_messages_params.get("limit") == 5


@pytest.mark.asyncio
async def test_session_query_extension_applies_default_limit_when_omitted(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "codex.sessions.list", "params": {}},
        )
        assert resp.status_code == 200
        assert dummy.last_sessions_params == {"limit": SESSION_QUERY_DEFAULT_LIMIT}

        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "codex.sessions.messages.list",
                "params": {"session_id": "s-1"},
            },
        )
        assert resp.status_code == 200
        assert dummy.last_messages_params == {"limit": SESSION_QUERY_DEFAULT_LIMIT}


@pytest.mark.asyncio
async def test_session_query_extension_rejects_non_array_upstream_payload(monkeypatch):
    import codex_a2a_server.app as app_module

    class WeirdPayloadClient(DummyCodexClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = {"foo": "bar"}  # no items

    monkeypatch.setattr(app_module, "CodexClient", WeirdPayloadClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "codex.sessions.list",
                "params": {},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["error"]["code"] == -32005
        assert payload["error"]["data"]["type"] == "UPSTREAM_PAYLOAD_ERROR"


@pytest.mark.asyncio
async def test_session_query_extension_session_title_is_extracted_or_placeholder(monkeypatch):
    import codex_a2a_server.app as app_module

    class TitlePayloadClient(DummyCodexClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = [{"id": "s-1", "title": "My Session"}]

    monkeypatch.setattr(app_module, "CodexClient", TitlePayloadClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "codex.sessions.list", "params": {}},
        )
        payload = resp.json()
        session = payload["result"]["items"][0]
        assert session["id"] == "s-1"
        assert session["metadata"]["shared"]["session"]["title"] == "My Session"


@pytest.mark.asyncio
async def test_session_query_extension_message_role_and_id_from_info(monkeypatch):
    import codex_a2a_server.app as app_module

    class InfoRoleClient(DummyCodexClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._messages_payload = [
                {
                    "info": {"id": "msg-1", "role": "user"},
                    "parts": [{"type": "text", "text": "hello"}],
                }
            ]

    monkeypatch.setattr(app_module, "CodexClient", InfoRoleClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "codex.sessions.messages.list",
                "params": {"session_id": "s-1"},
            },
        )
        payload = resp.json()
        message = payload["result"]["items"][0]
        assert message["messageId"] == "msg-1"
        assert message["role"] == "user"
        assert message["parts"][0]["text"] == "hello"


@pytest.mark.asyncio
async def test_session_query_extension_accepts_top_level_list_payload(monkeypatch):
    import codex_a2a_server.app as app_module

    class ListPayloadClient(DummyCodexClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = [{"id": "s-1", "title": "s1"}]
            self._messages_payload = [
                {
                    "info": {"id": "m-1", "role": "assistant"},
                    "parts": [{"type": "text", "text": "SECRET_HISTORY"}],
                }
            ]

    monkeypatch.setattr(app_module, "CodexClient", ListPayloadClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "codex.sessions.list", "params": {}},
        )
        payload = resp.json()
        assert payload["result"]["items"][0]["id"] == "s-1"

        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "codex.sessions.messages.list",
                "params": {"session_id": "s-1"},
            },
        )
        payload = resp.json()
        assert payload["result"]["items"][0]["contextId"] == "s-1"
        assert payload["result"]["items"][0]["parts"][0]["text"] == "SECRET_HISTORY"


@pytest.mark.asyncio
async def test_session_query_extension_rejects_non_list_wrapped_payload(monkeypatch):
    import codex_a2a_server.app as app_module

    class AltKeyPayloadClient(DummyCodexClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = {"sessions": [{"id": "s-1"}]}
            self._messages_payload = {"messages": [{"id": "m-1", "text": "SECRET_HISTORY"}]}

    monkeypatch.setattr(app_module, "CodexClient", AltKeyPayloadClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "codex.sessions.list", "params": {}},
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32005
        assert payload["error"]["data"]["type"] == "UPSTREAM_PAYLOAD_ERROR"

        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "codex.sessions.messages.list",
                "params": {"session_id": "s-1"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32005
        assert payload["error"]["data"]["type"] == "UPSTREAM_PAYLOAD_ERROR"


@pytest.mark.asyncio
async def test_session_query_extension_rejects_cursor_limit(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "codex.sessions.list",
                "params": {"cursor": "abc", "limit": 10},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
        assert payload["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_session_query_extension_rejects_page_size_pagination(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "codex.sessions.list",
                "params": {"page": 1, "size": 1000},
            },
        )
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
        assert payload["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_session_query_extension_rejects_limit_above_declared_max(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "codex.sessions.list",
                "params": {"limit": SESSION_QUERY_MAX_LIMIT + 1},
            },
        )
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
        assert payload["error"]["code"] == -32602
        assert payload["error"]["message"] == f"limit must be <= {SESSION_QUERY_MAX_LIMIT}"


@pytest.mark.asyncio
async def test_session_query_extension_maps_404_to_session_not_found(monkeypatch):
    import codex_a2a_server.app as app_module

    class NotFoundCodexClient(DummyCodexClient):
        async def list_messages(self, session_id: str, *, params=None):
            request = httpx.Request("GET", "http://codex/session/x/message")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("Not Found", request=request, response=response)

    monkeypatch.setattr(app_module, "CodexClient", NotFoundCodexClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "codex.sessions.messages.list",
                "params": {"session_id": "s-404"},
            },
        )
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 2
        assert payload["error"]["code"] == -32001
        assert payload["error"]["data"]["type"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_session_query_extension_does_not_log_response_bodies(monkeypatch, caplog):
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyCodexClient)
    caplog.set_level(logging.DEBUG, logger="codex_a2a_server.http_middlewares")

    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=True, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "codex.sessions.messages.list",
                "params": {"session_id": "s-1"},
            },
        )
        assert resp.status_code == 200

    # The response contains SECRET_HISTORY but the log middleware must not print bodies for
    # codex.sessions.* operations.
    assert "SECRET_HISTORY" not in caplog.text


@pytest.mark.asyncio
async def test_session_control_prompt_async_returns_turn_handle(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "codex.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "parts": [{"type": "text", "text": "summarize this repo"}],
                        "messageID": "msg-21",
                    },
                    "metadata": {"codex": {"directory": "/workspace"}},
                },
            },
        )
        payload = resp.json()
        assert payload["result"] == {"ok": True, "session_id": "s-1", "turn_id": "turn-1"}
        assert dummy.last_prompt_async == {
            "session_id": "s-1",
            "request": {
                "parts": [{"type": "text", "text": "summarize this repo"}],
                "messageID": "msg-21",
            },
            "directory": "/workspace",
        }


@pytest.mark.asyncio
async def test_session_control_command_maps_response_to_a2a_message(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 22,
                "method": "codex.sessions.command",
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "command": "plan",
                        "arguments": "show current work",
                        "messageID": "cmd-msg-1",
                    },
                    "metadata": {"codex": {"directory": "/workspace/app"}},
                },
            },
        )
        payload = resp.json()
        item = payload["result"]["item"]
        assert item["contextId"] == "s-1"
        assert item["messageId"] == "cmd-msg-1"
        assert item["parts"][0]["text"] == "command:plan show current work"
        assert dummy.last_command == {
            "session_id": "s-1",
            "request": {
                "command": "plan",
                "arguments": "show current work",
                "messageID": "cmd-msg-1",
            },
            "directory": "/workspace/app",
        }


@pytest.mark.asyncio
async def test_session_control_command_accepts_missing_arguments(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 221,
                "method": "codex.sessions.command",
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "command": "plan",
                        "messageID": "cmd-msg-2",
                    },
                },
            },
        )
        payload = resp.json()
        item = payload["result"]["item"]
        assert item["contextId"] == "s-1"
        assert item["messageId"] == "cmd-msg-2"
        assert item["parts"][0]["text"] == "command:plan"
        assert dummy.last_command == {
            "session_id": "s-1",
            "request": {
                "command": "plan",
                "messageID": "cmd-msg-2",
            },
            "directory": None,
        }


@pytest.mark.asyncio
async def test_session_control_shell_maps_response_to_a2a_message(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 23,
                "method": "codex.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"command": "pwd"},
                },
            },
        )
        payload = resp.json()
        item = payload["result"]["item"]
        assert item["contextId"] == "s-1"
        assert item["messageId"] == "shell-1"
        assert item["parts"][0]["text"] == "stdout\n$ pwd"
        assert dummy.last_shell == {
            "session_id": "s-1",
            "request": {"command": "pwd"},
            "directory": None,
        }


@pytest.mark.asyncio
async def test_session_control_rejects_invalid_request_shape(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}

        prompt_resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 24,
                "method": "codex.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {"parts": [{"type": "file", "text": "unsupported"}]},
                },
            },
        )
        prompt_payload = prompt_resp.json()
        assert prompt_payload["error"]["code"] == -32602
        assert prompt_payload["error"]["data"]["field"] == "request.parts[0].type"

        shell_resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 25,
                "method": "codex.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"command": "   "},
                },
            },
        )
        shell_payload = shell_resp.json()
        assert shell_payload["error"]["code"] == -32602
        assert shell_payload["error"]["data"]["field"] == "request.command"


@pytest.mark.asyncio
async def test_session_control_shell_method_is_not_exposed_when_disabled(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=False,
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=False,
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 26,
                "method": "codex.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"command": "pwd"},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32601
        assert payload["error"]["data"]["type"] == "METHOD_NOT_SUPPORTED"
        assert payload["error"]["data"]["method"] == "codex.sessions.shell"
        assert "codex.sessions.shell" not in payload["error"]["data"]["supported_methods"]
        assert dummy.last_shell is None


@pytest.mark.asyncio
async def test_session_control_rejects_invalid_metadata_directory(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_allow_directory_override=False,
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_allow_directory_override=False,
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 26,
                "method": "codex.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {"parts": [{"type": "text", "text": "hello"}]},
                    "metadata": {"codex": {"directory": "/tmp/not-allowed"}},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "metadata.codex.directory"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_permission_reply(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.prime_interrupt_request("perm-1", interrupt_type="permission")
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "a2a.interrupt.permission.reply",
                "params": {
                    "request_id": "perm-1",
                    "reply": "once",
                    "message": "approved by operator",
                    "metadata": {"codex": {"directory": "/workspace"}},
                },
            },
        )
        payload = resp.json()
        assert payload.get("error") is None
        assert payload["result"]["ok"] is True
        assert payload["result"]["request_id"] == "perm-1"
        assert payload["result"]["reply"] == "once"
        assert len(dummy.permission_reply_calls) == 1
        assert dummy.permission_reply_calls[0]["request_id"] == "perm-1"
        assert dummy.permission_reply_calls[0]["reply"] == "once"
        assert dummy.permission_reply_calls[0]["directory"] == "/workspace"
        status, _ = dummy.resolve_interrupt_request("perm-1")
        assert status == "missing"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_rejects_legacy_permission_fields(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 111,
                "method": "a2a.interrupt.permission.reply",
                "params": {"requestID": "perm-legacy", "decision": "allow"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_interrupt_callback_extension_question_reply_and_reject(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.prime_interrupt_request("q-1", interrupt_type="question")
    dummy.prime_interrupt_request("q-2", interrupt_type="question")
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        reply_resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 12,
                "method": "a2a.interrupt.question.reply",
                "params": {"request_id": "q-1", "answers": [["A"], ["B"]]},
            },
        )
        reply_payload = reply_resp.json()
        assert reply_payload["result"]["ok"] is True
        assert reply_payload["result"]["request_id"] == "q-1"
        assert dummy.question_reply_calls[0]["answers"] == [["A"], ["B"]]

        reject_resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 13,
                "method": "a2a.interrupt.question.reject",
                "params": {"request_id": "q-2"},
            },
        )
        reject_payload = reject_resp.json()
        assert reject_payload["result"]["ok"] is True
        assert dummy.question_reject_calls[0]["request_id"] == "q-2"
        assert dummy.resolve_interrupt_request("q-1")[0] == "missing"
        assert dummy.resolve_interrupt_request("q-2")[0] == "missing"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_maps_404_to_interrupt_not_found(monkeypatch):
    import codex_a2a_server.app as app_module

    class NotFoundInterruptClient(DummyCodexClient):
        async def permission_reply(
            self,
            request_id: str,
            *,
            reply: str,
            message: str | None = None,
            directory: str | None = None,
        ) -> bool:
            del request_id, reply, message, directory
            request = httpx.Request("POST", "http://codex/permission/x/reply")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("Not Found", request=request, response=response)

    dummy = NotFoundInterruptClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.prime_interrupt_request("perm-404", interrupt_type="permission")
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 14,
                "method": "a2a.interrupt.permission.reply",
                "params": {"request_id": "perm-404", "reply": "reject"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32004
        assert payload["error"]["data"]["type"] == "INTERRUPT_REQUEST_NOT_FOUND"
        assert dummy.resolve_interrupt_request("perm-404")[0] == "missing"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_returns_not_found_for_missing_local_request(
    monkeypatch,
):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 15,
                "method": "a2a.interrupt.permission.reply",
                "params": {"request_id": "perm-missing", "reply": "reject"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32004
        assert payload["error"]["data"]["type"] == "INTERRUPT_REQUEST_NOT_FOUND"
        assert dummy.permission_reply_calls == []


@pytest.mark.asyncio
async def test_interrupt_callback_extension_returns_expired_for_stale_request(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.prime_interrupt_request("perm-expired", interrupt_type="permission", created_at=1.0)
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 16,
                "method": "a2a.interrupt.permission.reply",
                "params": {"request_id": "perm-expired", "reply": "reject"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32007
        assert payload["error"]["data"]["type"] == "INTERRUPT_REQUEST_EXPIRED"
        assert dummy.resolve_interrupt_request("perm-expired")[0] == "missing"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_rejects_interrupt_type_mismatch(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.prime_interrupt_request("perm-type", interrupt_type="permission")
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 17,
                "method": "a2a.interrupt.question.reply",
                "params": {"request_id": "perm-type", "answers": [["A"]]},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32008
        assert payload["error"]["data"]["type"] == "INTERRUPT_TYPE_MISMATCH"
        assert payload["error"]["data"]["expected_interrupt_type"] == "question"
        assert payload["error"]["data"]["actual_interrupt_type"] == "permission"
        assert dummy.question_reply_calls == []
        assert dummy.resolve_interrupt_request("perm-type")[0] == "active"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_masks_owner_mismatch_as_not_found(monkeypatch):
    import codex_a2a_server.app as app_module

    dummy = DummyCodexClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.prime_interrupt_request("perm-owned", interrupt_type="permission", session_id="ses-owned")
    monkeypatch.setattr(app_module, "CodexClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    @app.middleware("http")
    async def inject_identity(request, call_next):  # noqa: ANN001
        request.state.user_identity = "user-1"
        return await call_next(request)

    await app.state.codex_executor.finalize_session_claim(
        identity="other-user",
        session_id="ses-owned",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 18,
                "method": "a2a.interrupt.permission.reply",
                "params": {"request_id": "perm-owned", "reply": "once"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32004
        assert payload["error"]["data"]["type"] == "INTERRUPT_REQUEST_NOT_FOUND"
        assert dummy.permission_reply_calls == []
        assert dummy.resolve_interrupt_request("perm-owned")[0] == "active"
