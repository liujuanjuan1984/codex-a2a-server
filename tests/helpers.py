from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, PropertyMock

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, MessageSendParams, Role, TextPart

from codex_a2a_server.codex_client import CodexClient, CodexMessage, InterruptRequestBinding
from codex_a2a_server.config import Settings

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "codex_base_url": "http://127.0.0.1:4096",
        "a2a_bearer_token": "test-token",
    }
    base.update(overrides)
    return Settings(**base)


def load_json_fixture(*relative_parts: str) -> Any:
    fixture_path = _FIXTURES_DIR.joinpath(*relative_parts)
    return json.loads(fixture_path.read_text(encoding="utf-8"))


async def replay_codex_notification_fixture(
    *relative_parts: str,
) -> tuple[dict[str, Any], list[dict]]:
    fixture = load_json_fixture(*relative_parts)
    client = CodexClient(make_settings(a2a_bearer_token="test-token", codex_timeout=1.0))
    events: list[dict] = []

    async def fake_enqueue(event: dict) -> None:
        events.append(event)

    client._enqueue_stream_event = fake_enqueue  # type: ignore[method-assign]
    for notification in fixture["notifications"]:
        await client._handle_notification(notification)
    return fixture, events


class DummyEventQueue:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def enqueue_event(self, event: Any) -> None:
        self.events.append(event)

    async def close(self) -> None:
        return None


def make_request_context_mock(
    *,
    task_id: str | None,
    context_id: str | None,
    identity: str | None = None,
    user_input: str = "",
    metadata: Any = None,
    message: Any = None,
    current_task: Any = None,
    call_context_enabled: bool = True,
) -> MagicMock:
    context = MagicMock(spec=RequestContext)
    context.task_id = task_id
    context.context_id = context_id
    context.get_user_input.return_value = user_input
    context.metadata = metadata
    context.message = message
    context.current_task = current_task
    if call_context_enabled:
        call_context = MagicMock(spec=ServerCallContext)
        call_context.state = {"identity": identity} if identity else {}
        context.call_context = call_context
    else:
        context.call_context = None
    return context


def configure_mock_client_runtime(
    client: Any,
    *,
    directory: str = "/tmp/workspace",
    settings_overrides: dict[str, Any] | None = None,
) -> None:
    overrides: dict[str, Any] = {
        "a2a_bearer_token": "test",
        "codex_base_url": "http://localhost",
        "a2a_allow_directory_override": True,
    }
    if settings_overrides:
        overrides.update(settings_overrides)
    type(client).directory = PropertyMock(return_value=directory)
    type(client).settings = PropertyMock(return_value=make_settings(**overrides))


def make_request_context(
    *,
    task_id: str,
    context_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    message_id: str = "req-1",
) -> RequestContext:
    message = Message(
        message_id=message_id,
        role=Role.user,
        parts=[TextPart(text=text)],
    )
    params = MessageSendParams(message=message, metadata=metadata)
    return RequestContext(request=params, task_id=task_id, context_id=context_id)


class DummyChatCodexClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.created_sessions = 0
        self.sent_session_ids: list[str] = []
        self.stream_timeout = None
        self.directory = None
        self.settings = settings or make_settings(
            a2a_bearer_token="test",
            codex_base_url="http://localhost",
        )

    async def close(self) -> None:
        return None

    async def create_session(
        self,
        title: str | None = None,
        *,
        directory: str | None = None,
    ) -> str:
        del title, directory
        self.created_sessions += 1
        return f"ses-created-{self.created_sessions}"

    async def send_message(
        self,
        session_id: str,
        text: str,
        *,
        directory: str | None = None,
        timeout_override=None,  # noqa: ANN001
    ) -> CodexMessage:
        del directory, timeout_override
        self.sent_session_ids.append(session_id)
        return CodexMessage(
            text=f"echo:{text}",
            session_id=session_id,
            message_id="m-1",
            raw={},
        )

    async def stream_events(self, stop_event=None, *, directory: str | None = None):  # noqa: ANN001
        del stop_event, directory
        for _ in ():
            yield {}

    def remember_interrupt_request(self, *, request_id: str, session_id: str) -> None:
        del request_id, session_id

    def resolve_interrupt_session(self, request_id: str) -> str | None:
        del request_id
        return None

    def discard_interrupt_request(self, request_id: str) -> None:
        del request_id


class DummySessionQueryCodexClient:
    def __init__(self, _settings: Settings) -> None:
        self.directory = "/workspace"
        self.settings = _settings
        self._sessions_payload = [{"id": "s-1", "title": "Session s-1"}]
        self._messages_payload = [
            {
                "info": {"id": "m-1", "role": "assistant"},
                "parts": [{"type": "text", "text": "SECRET_HISTORY"}],
            }
        ]
        self.last_sessions_params = None
        self.last_messages_params = None
        self.last_prompt_async: dict[str, Any] | None = None
        self.last_command: dict[str, Any] | None = None
        self.last_shell: dict[str, Any] | None = None
        self.permission_reply_calls: list[dict[str, Any]] = []
        self.question_reply_calls: list[dict[str, Any]] = []
        self.question_reject_calls: list[dict[str, Any]] = []
        self._interrupt_requests: dict[str, InterruptRequestBinding] = {}

    async def close(self) -> None:
        return None

    async def list_sessions(self, *, params=None):
        self.last_sessions_params = params
        return self._sessions_payload

    async def list_messages(self, session_id: str, *, params=None):
        assert session_id
        self.last_messages_params = params
        return self._messages_payload

    async def session_prompt_async(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> dict[str, Any]:
        self.last_prompt_async = {
            "session_id": session_id,
            "request": request,
            "directory": directory,
        }
        return {"ok": True, "session_id": session_id, "turn_id": "turn-1"}

    async def session_command(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> CodexMessage:
        self.last_command = {
            "session_id": session_id,
            "request": request,
            "directory": directory,
        }
        return CodexMessage(
            text=f"command:{request['command']} {request.get('arguments', '')}".strip(),
            session_id=session_id,
            message_id=request.get("messageID") or "cmd-1",
            raw={"request": request},
        )

    async def session_shell(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> dict[str, Any]:
        self.last_shell = {
            "session_id": session_id,
            "request": request,
            "directory": directory,
        }
        return {
            "info": {"id": "shell-1", "role": "assistant"},
            "parts": [{"type": "text", "text": f"stdout\n$ {request['command']}"}],
            "raw": {"request": request},
        }

    async def permission_reply(
        self,
        request_id: str,
        *,
        reply: str,
        message: str | None = None,
        directory: str | None = None,
    ) -> bool:
        self.permission_reply_calls.append(
            {
                "request_id": request_id,
                "reply": reply,
                "message": message,
                "directory": directory,
            }
        )
        return True

    async def question_reply(
        self,
        request_id: str,
        *,
        answers: list[list[str]],
        directory: str | None = None,
    ) -> bool:
        self.question_reply_calls.append(
            {"request_id": request_id, "answers": answers, "directory": directory}
        )
        return True

    async def question_reject(
        self,
        request_id: str,
        *,
        directory: str | None = None,
    ) -> bool:
        self.question_reject_calls.append({"request_id": request_id, "directory": directory})
        return True

    def prime_interrupt_request(
        self,
        request_id: str,
        *,
        interrupt_type: str,
        session_id: str = "ses-1",
        created_at: float = 0.0,
        provider_method: str | None = None,
    ) -> None:
        resolved_method = provider_method
        if resolved_method is None:
            resolved_method = (
                "item/tool/requestUserInput"
                if interrupt_type == "question"
                else "item/commandExecution/requestApproval"
            )
        self._interrupt_requests[request_id] = InterruptRequestBinding(
            request_id=request_id,
            interrupt_type=interrupt_type,
            session_id=session_id,
            created_at=created_at,
            provider_method=resolved_method,
        )

    def resolve_interrupt_request(
        self,
        request_id: str,
    ) -> tuple[str, InterruptRequestBinding | None]:
        binding = self._interrupt_requests.get(request_id)
        if binding is None:
            return "missing", None
        if binding.created_at == 0.0:
            return "active", binding
        self._interrupt_requests.pop(request_id, None)
        return "expired", binding

    def discard_interrupt_request(self, request_id: str) -> None:
        self._interrupt_requests.pop(request_id, None)
