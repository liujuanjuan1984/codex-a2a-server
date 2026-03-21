from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


class CodexStartupPrerequisiteError(RuntimeError):
    """Raised when local Codex prerequisites are not satisfied."""


@dataclass(frozen=True)
class CodexMessage:
    text: str
    session_id: str
    message_id: str | None
    raw: dict[str, Any]


class CodexRPCError(RuntimeError):
    def __init__(self, *, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass
class _PendingRpcRequest:
    request_id: str
    method: str
    future: asyncio.Future[Any]
    correlation_id: str | None


@dataclass
class _TurnTracker:
    thread_id: str
    turn_id: str
    completed: asyncio.Event = field(default_factory=asyncio.Event)
    text_chunks: list[str] = field(default_factory=list)
    message_id: str | None = None
    raw_turn: dict[str, Any] | None = None
    error: str | None = None

    @property
    def text(self) -> str:
        return "".join(self.text_chunks)
