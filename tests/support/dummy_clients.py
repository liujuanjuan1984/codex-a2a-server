from __future__ import annotations

from typing import Any

from codex_a2a_server.config import Settings
from codex_a2a_server.upstream.client import CodexMessage
from codex_a2a_server.upstream.interrupts import InterruptRequestBinding
from tests.support.settings import make_settings


class DummyChatCodexClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.created_sessions = 0
        self.sent_session_ids: list[str] = []
        self.stream_timeout = None
        self.directory = None
        self.settings = settings or make_settings(
            a2a_bearer_token="test",
        )

    async def close(self) -> None:
        return None

    async def startup_preflight(self) -> None:
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

    def discard_interrupt_request(self, request_id: str) -> None:
        del request_id


class DummySessionQueryCodexClient:
    def __init__(self, _settings: Settings) -> None:
        self.directory = "/workspace"
        self.settings = _settings
        self._sessions_payload: Any = [{"id": "s-1", "title": "Session s-1"}]
        self._messages_payload: Any = [
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

    async def session_prompt_async(self, session_id: str, *, request=None, directory=None):
        self.last_prompt_async = {
            "session_id": session_id,
            "request": request,
            "directory": directory,
        }
        return {"ok": True, "session_id": session_id, "turn_id": "turn-1"}

    async def session_command(self, session_id: str, *, request=None, directory=None) -> CodexMessage:
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

    async def session_shell(self, session_id: str, *, request=None, directory=None):
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
            {
                "request_id": request_id,
                "answers": answers,
                "directory": directory,
            }
        )
        return True

    async def question_reject(
        self,
        request_id: str,
        *,
        directory: str | None = None,
    ) -> bool:
        self.question_reject_calls.append(
            {
                "request_id": request_id,
                "directory": directory,
            }
        )
        return True

    def discard_interrupt_request(self, request_id: str) -> None:
        self._interrupt_requests.pop(request_id, None)

    def prime_interrupt_request(
        self,
        request_id: str,
        *,
        interrupt_type: str,
        session_id: str = "ses-1",
        created_at: float = 0.0,
    ) -> None:
        self._interrupt_requests[request_id] = InterruptRequestBinding(
            request_id=request_id,
            interrupt_type=interrupt_type,
            session_id=session_id,
            created_at=created_at,
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
