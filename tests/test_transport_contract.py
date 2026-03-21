import asyncio
import hashlib
import logging
import uuid
from unittest.mock import MagicMock

import httpx
import pytest
from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.types import TransportProtocol
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request

from codex_a2a_server.app import build_agent_card, create_app
from tests.helpers import DummyChatCodexClient, make_settings


async def _empty_async_stream():
    if asyncio.current_task() is None:
        yield {}


def test_agent_card_declares_dual_stack_with_http_json_preferred() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token"))

    assert card.preferred_transport == TransportProtocol.http_json
    transports = {iface.transport for iface in card.additional_interfaces or []}
    assert TransportProtocol.http_json in transports
    assert TransportProtocol.jsonrpc in transports


def test_rest_subscription_route_matches_current_sdk_contract() -> None:
    app = create_app(make_settings(a2a_bearer_token="test-token"))
    route_paths = {route.path for route in app.router.routes if hasattr(route, "path")}

    assert "/health" in route_paths
    assert "/v1/tasks/{id}:subscribe" in route_paths
    assert "/v1/tasks/{id}:resubscribe" not in route_paths


def test_health_route_can_be_disabled() -> None:
    app = create_app(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_enable_health_endpoint=False,
        )
    )
    route_paths = {route.path for route in app.router.routes if hasattr(route, "path")}

    assert "/health" not in route_paths


def test_create_app_resets_sse_app_status() -> None:
    from sse_starlette.sse import AppStatus

    original_should_exit = AppStatus.should_exit
    original_should_exit_event = AppStatus.should_exit_event
    try:
        AppStatus.should_exit = True
        AppStatus.should_exit_event = asyncio.Event()

        create_app(make_settings(a2a_bearer_token="test-token"))

        assert AppStatus.should_exit is False
        assert AppStatus.should_exit_event is None
    finally:
        AppStatus.should_exit = original_should_exit
        AppStatus.should_exit_event = original_should_exit_event


@pytest.mark.asyncio
async def test_streaming_route_uses_sdk_default_sse_keepalive() -> None:
    settings = make_settings(a2a_bearer_token="test-token")
    context_builder = MagicMock()
    context_builder.build.return_value = MagicMock()

    async def receive() -> dict:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/message:stream",
            "raw_path": b"/v1/message:stream",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        },
        receive,
    )

    async def stream_method(_request: Request, _context):
        async for item in _empty_async_stream():
            yield item

    adapter = RESTAdapter(
        agent_card=build_agent_card(settings),
        http_handler=MagicMock(),
        context_builder=context_builder,
    )
    response = await adapter._handle_streaming_request(stream_method, request)

    assert response.ping_interval == EventSourceResponse.DEFAULT_PING_INTERVAL


def test_create_app_propagates_stream_idle_diagnostic_setting(monkeypatch) -> None:
    import codex_a2a_server.app as app_module

    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_stream_idle_diagnostic_seconds=42.0,
    )
    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)

    app = app_module.create_app(settings)

    assert app.state.codex_executor._stream_idle_diagnostic_seconds == 42.0


def test_openapi_rest_message_routes_include_schema_examples_and_extension_contracts() -> None:
    app = create_app(make_settings(a2a_bearer_token="test-token"))
    openapi = app.openapi()
    paths = openapi["paths"]

    expected: dict[str, str] = {
        "/v1/message:send": "#/components/schemas/SendMessageRequest",
        "/v1/message:stream": "#/components/schemas/SendStreamingMessageRequest",
    }
    for path, expected_schema_ref in expected.items():
        post = paths[path]["post"]
        assert post["summary"] in {"Send Message (HTTP+JSON)", "Stream Message (HTTP+JSON)"}
        content = post.get("requestBody", {}).get("content", {}).get("application/json", {})
        assert content.get("schema", {}).get("$ref") == expected_schema_ref
        examples = content.get("examples")
        assert isinstance(examples, dict)
        assert "basic_message" in examples
        assert "continue_session" in examples
        contracts = post.get("x-a2a-extension-contracts")
        assert isinstance(contracts, dict)
        assert "session_binding" in contracts

    stream_contract = paths["/v1/message:stream"]["post"].get("x-a2a-streaming")
    assert isinstance(stream_contract, dict)

    root_contracts = paths["/"]["post"].get("x-a2a-extension-contracts")
    assert isinstance(root_contracts, dict)
    assert "wire_contract" in root_contracts


def test_openapi_jsonrpc_examples_include_core_and_extension_methods() -> None:
    app = create_app(make_settings(a2a_bearer_token="test-token"))
    openapi = app.openapi()
    post = openapi["paths"]["/"]["post"]
    example_values = (
        post.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("examples", {})
        .values()
    )
    methods = {value.get("value", {}).get("method") for value in example_values}
    assert "message/send" in methods
    assert "message/stream" in methods
    assert "codex.sessions.list" in methods
    assert "codex.sessions.messages.list" in methods
    assert "codex.sessions.prompt_async" in methods
    assert "codex.sessions.command" in methods
    assert "a2a.interrupt.permission.reply" in methods


@pytest.mark.asyncio
async def test_health_endpoint_requires_bearer_token(monkeypatch) -> None:
    import codex_a2a_server.app as app_module

    settings = make_settings(a2a_bearer_token="test-token")
    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 401
    assert resp.json() == {"error": "Unauthorized"}


@pytest.mark.asyncio
async def test_health_endpoint_with_bearer_token_reports_profile(monkeypatch) -> None:
    import codex_a2a_server.app as app_module

    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_enable_session_shell=False,
        a2a_interrupt_request_ttl_seconds=90,
    )
    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(settings)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health", headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "service": "codex-a2a-server",
        "version": settings.a2a_version,
        "profile": {
            "profile_id": "codex-a2a-single-tenant-coding-v1",
            "deployment": {
                "id": "single_tenant_shared_workspace",
                "single_tenant": True,
                "shared_workspace_across_consumers": True,
                "tenant_isolation": "none",
            },
            "runtime_features": {
                "directory_binding": {
                    "allow_override": True,
                    "scope": "workspace_root_or_descendant",
                },
                "session_shell": {
                    "enabled": False,
                    "availability": "disabled",
                    "toggle": "A2A_ENABLE_SESSION_SHELL",
                },
                "interrupts": {
                    "request_ttl_seconds": 90,
                },
                "service_features": {
                    "streaming": {
                        "enabled": True,
                        "availability": "always",
                    },
                    "health_endpoint": {
                        "enabled": True,
                        "availability": "enabled",
                        "toggle": "A2A_ENABLE_HEALTH_ENDPOINT",
                    },
                },
                "execution_environment": {
                    "sandbox": {
                        "mode": "unknown",
                        "filesystem_scope": "unknown",
                    },
                    "network": {
                        "access": "unknown",
                    },
                    "approval": {
                        "policy": "unknown",
                    },
                    "write_access": {
                        "scope": "unknown",
                    },
                },
            },
        },
    }


@pytest.mark.asyncio
async def test_app_lifespan_runs_codex_startup_preflight() -> None:
    calls: list[str] = []

    class PreflightClient(DummyChatCodexClient):
        async def startup_preflight(self) -> None:
            calls.append("startup_preflight")

    import codex_a2a_server.app as app_module

    original_client = app_module.CodexClient
    app_module.CodexClient = PreflightClient
    try:
        app = app_module.create_app(make_settings(a2a_bearer_token="test-token"))
        async with app.router.lifespan_context(app):
            pass
    finally:
        app_module.CodexClient = original_client

    assert calls == ["startup_preflight"]


@pytest.mark.asyncio
async def test_dual_stack_send_accepts_transport_native_payloads(monkeypatch) -> None:
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    rest_payload = {
        "message": {
            "messageId": "m-rest",
            "role": "ROLE_USER",
            "content": [{"text": "hello from rest"}],
        }
    }
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "messageId": "m-rpc",
                "role": "user",
                "parts": [{"kind": "text", "text": "hello from jsonrpc"}],
            }
        },
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rest_resp = await client.post("/v1/message:send", headers=headers, json=rest_payload)
        assert rest_resp.status_code == 200

        rpc_resp = await client.post("/", headers=headers, json=rpc_payload)
        assert rpc_resp.status_code == 200
        assert rpc_resp.json().get("error") is None


@pytest.mark.asyncio
async def test_dual_stack_send_rejects_cross_transport_payload_shapes(monkeypatch) -> None:
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    rest_with_jsonrpc_shape = {
        "message": {
            "messageId": "m-rest-cross",
            "role": "user",
            "parts": [{"kind": "text", "text": "hello"}],
        }
    }
    full_jsonrpc_envelope = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "message/send",
        "params": {
            "message": {
                "messageId": "m-rest-cross-envelope",
                "role": "user",
                "parts": [{"kind": "text", "text": "hello from envelope"}],
            }
        },
    }
    rpc_with_rest_shape = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "message/send",
        "params": {
            "message": {
                "messageId": "m-rpc-cross",
                "role": "ROLE_USER",
                "content": [{"text": "hello"}],
            }
        },
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rest_resp = await client.post(
            "/v1/message:send",
            headers=headers,
            json=rest_with_jsonrpc_shape,
        )
        assert rest_resp.status_code == 400
        assert "Invalid HTTP+JSON payload" in rest_resp.text

        rest_envelope_resp = await client.post(
            "/v1/message:send",
            headers=headers,
            json=full_jsonrpc_envelope,
        )
        assert rest_envelope_resp.status_code == 400
        assert "Invalid HTTP+JSON payload" in rest_envelope_resp.text

        rpc_resp = await client.post("/", headers=headers, json=rpc_with_rest_shape)
        assert rpc_resp.status_code == 200
        payload = rpc_resp.json()
        assert payload["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_jsonrpc_unsupported_method_returns_supported_method_contract(monkeypatch) -> None:
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    settings = make_settings(a2a_bearer_token="test-token")
    app = app_module.create_app(settings)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 40, "method": "SendMessage", "params": {}},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["code"] == -32601
    assert payload["error"]["message"] == "Unsupported method: SendMessage"
    assert payload["error"]["data"]["type"] == "METHOD_NOT_SUPPORTED"
    assert payload["error"]["data"]["method"] == "SendMessage"
    assert payload["error"]["data"]["protocol_version"] == settings.a2a_protocol_version
    assert "message/send" in payload["error"]["data"]["supported_methods"]
    assert "codex.sessions.list" in payload["error"]["data"]["supported_methods"]


@pytest.mark.asyncio
async def test_jsonrpc_disabled_shell_reports_current_supported_methods(monkeypatch) -> None:
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_enable_session_shell=False,
    )
    app = app_module.create_app(settings)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 41,
                "method": "codex.sessions.shell",
                "params": {"session_id": "s-1", "request": {"command": "pwd"}},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["code"] == -32601
    assert payload["error"]["data"]["type"] == "METHOD_NOT_SUPPORTED"
    assert payload["error"]["data"]["method"] == "codex.sessions.shell"
    assert "codex.sessions.shell" not in payload["error"]["data"]["supported_methods"]


@pytest.mark.asyncio
async def test_subscribe_missing_task_returns_controlled_404(monkeypatch) -> None:
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/tasks/task-missing:subscribe", headers=headers)

    assert response.status_code == 404
    assert response.json() == {"error": "Task not found", "task_id": "task-missing"}


def _rest_message_payload() -> dict:
    return {
        "message": {
            "messageId": "m-rest",
            "role": "ROLE_USER",
            "content": [{"text": "hello from rest"}],
        }
    }


def _jsonrpc_message_send_payload(text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "message/send",
        "params": {
            "message": {
                "messageId": "m-rpc",
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }


@pytest.mark.asyncio
async def test_log_payloads_keeps_body_for_rest_handler(monkeypatch, caplog) -> None:
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token", a2a_log_payloads=True))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    with caplog.at_level(logging.DEBUG, logger="codex_a2a_server.http_middlewares"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/message:send",
                headers=headers,
                json=_rest_message_payload(),
            )

            assert resp.status_code == 200

    assert any(
        "A2A request POST /v1/message:send body=" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_log_payloads_streaming_response_path(monkeypatch, caplog) -> None:
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token", a2a_log_payloads=True))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    with caplog.at_level(logging.DEBUG, logger="codex_a2a_server.http_middlewares"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST", "/v1/message:stream", headers=headers, json=_rest_message_payload()
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        break

    assert any(
        "A2A response /v1/message:stream status=200" in record.message
        or "A2A response /v1/message:stream streaming" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_log_payloads_omits_non_json_request_body(monkeypatch, caplog) -> None:
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token", a2a_log_payloads=True))
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/octet-stream",
    }

    with caplog.at_level(logging.DEBUG, logger="codex_a2a_server.http_middlewares"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/", headers=headers, content=b"\x00\x01\x02\x03")
            assert resp.status_code < 500

    assert any(
        "body=[omitted non-json content-type=application/octet-stream]" in record.message
        for record in caplog.records
    )
    assert "\\x00\\x01\\x02\\x03" not in caplog.text


@pytest.mark.asyncio
async def test_log_payloads_omits_text_plain_request_body(monkeypatch, caplog) -> None:
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token", a2a_log_payloads=True))
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "text/plain",
    }
    body = (
        '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":'
        '{"messageId":"m","role":"user","parts":[{"kind":"text","text":"secret"}]}}}'
    )

    with caplog.at_level(logging.DEBUG, logger="codex_a2a_server.http_middlewares"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/", headers=headers, content=body)
            assert resp.status_code < 500

    assert any(
        "body=[omitted non-json content-type=text/plain]" in record.message
        for record in caplog.records
    )
    assert "secret" not in caplog.text


@pytest.mark.asyncio
async def test_log_payloads_omits_when_content_length_missing(monkeypatch, caplog) -> None:
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_log_payloads=True,
            a2a_log_body_limit=64,
        )
    )
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
    }
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":'
        b'{"messageId":"m","role":"user","parts":[{"kind":"text","text":"missing-cl"}]}}}'
    )

    async def _body_stream():
        yield body

    with caplog.at_level(logging.DEBUG, logger="codex_a2a_server.http_middlewares"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/",
                headers=headers,
                content=_body_stream(),
            )
            assert resp.status_code == 200

    assert any(
        "body=[omitted missing content-length with limit=64]" in record.message
        for record in caplog.records
    )
    assert any(
        "body=[omitted request_missing content-length with limit=64]" in record.message
        for record in caplog.records
    )
    assert "missing-cl" not in caplog.text


@pytest.mark.asyncio
async def test_log_payloads_omits_oversized_request_body(monkeypatch, caplog) -> None:
    import codex_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_log_payloads=True,
            a2a_log_body_limit=64,
        )
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    oversized_text = "x" * 512

    with caplog.at_level(logging.DEBUG, logger="codex_a2a_server.http_middlewares"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/",
                headers=headers,
                json=_jsonrpc_message_send_payload(oversized_text),
            )
            assert resp.status_code == 200

    assert any(
        "body=[omitted content-length=" in record.message and "exceeds limit=64" in record.message
        for record in caplog.records
    )
    assert oversized_text not in caplog.text


@pytest.mark.asyncio
async def test_request_logs_reuse_supplied_correlation_id(monkeypatch, caplog) -> None:
    import codex_a2a_server.app as app_module

    expected_identity = f"bearer:{hashlib.sha256(b'test-token').hexdigest()[:12]}"
    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "X-Request-Id": "corr-user-supplied",
    }

    with caplog.at_level(logging.DEBUG):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/message:send",
                headers=headers,
                json=_rest_message_payload(),
            )

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "corr-user-supplied"

    relevant = [
        record
        for record in caplog.records
        if record.name
        in {
            "codex_a2a_server.http_middlewares",
            "codex_a2a_server.request_handler",
            "codex_a2a_server.agent",
        }
    ]
    assert relevant
    assert any("A2A request started" in record.message for record in relevant)
    assert any("A2A request completed" in record.message for record in relevant)
    assert any("A2A message request started" in record.message for record in relevant)
    assert any(
        f"Received message identity={expected_identity}" in record.message for record in relevant
    )
    assert {record.correlation_id for record in relevant} == {"corr-user-supplied"}
    assert "Bearer test-token" not in caplog.text
    assert "test-token" not in caplog.text


@pytest.mark.asyncio
async def test_request_logs_generate_correlation_id_for_stream_requests(
    monkeypatch,
    caplog,
) -> None:
    import codex_a2a_server.app as app_module

    expected_identity = f"bearer:{hashlib.sha256(b'test-token').hexdigest()[:12]}"
    monkeypatch.setattr(app_module, "CodexClient", DummyChatCodexClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    with caplog.at_level(logging.DEBUG):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/v1/message:stream",
                headers=headers,
                json=_rest_message_payload(),
            ) as response:
                assert response.status_code == 200
                generated = response.headers["X-Request-Id"]
                uuid.UUID(generated)
                async for _chunk in response.aiter_bytes():
                    break

    relevant = [
        record
        for record in caplog.records
        if record.name
        in {
            "codex_a2a_server.http_middlewares",
            "codex_a2a_server.request_handler",
            "codex_a2a_server.streaming",
            "codex_a2a_server.agent",
        }
    ]
    assert relevant
    assert any("A2A request started" in record.message for record in relevant)
    assert any("A2A stream request started" in record.message for record in relevant)
    assert any("Codex event stream started" in record.message for record in relevant)
    assert any(
        f"Received message identity={expected_identity}" in record.message for record in relevant
    )
    assert {record.correlation_id for record in relevant} == {generated}
    assert "Bearer test-token" not in caplog.text
    assert "test-token" not in caplog.text
