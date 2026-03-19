from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any
from uuid import uuid4

CORRELATION_ID_HEADER = "X-Request-Id"
_MISSING_CORRELATION_ID = "-"
_current_correlation_id: ContextVar[str | None] = ContextVar(
    "codex_a2a_server_correlation_id",
    default=None,
)
_log_record_factory_installed = False


def get_correlation_id() -> str | None:
    return _current_correlation_id.get()


def resolve_correlation_id(header_value: str | None) -> str:
    if isinstance(header_value, str):
        normalized = header_value.strip()
        if normalized:
            return normalized
    return str(uuid4())


def set_correlation_id(correlation_id: str | None) -> Token[str | None]:
    normalized = correlation_id.strip() if isinstance(correlation_id, str) else None
    return _current_correlation_id.set(normalized or None)


def reset_correlation_id(token: Token[str | None]) -> None:
    _current_correlation_id.reset(token)


@contextmanager
def bind_correlation_id(correlation_id: str | None) -> Iterator[None]:
    token = set_correlation_id(correlation_id)
    try:
        yield
    finally:
        reset_correlation_id(token)


def install_log_record_factory() -> None:
    global _log_record_factory_installed
    if _log_record_factory_installed:
        return

    current_factory = logging.getLogRecordFactory()

    def _factory(*args: Any, **kwargs: Any):
        record = current_factory(*args, **kwargs)
        if not hasattr(record, "correlation_id"):
            record.correlation_id = get_correlation_id() or _MISSING_CORRELATION_ID
        return record

    logging.setLogRecordFactory(_factory)
    _log_record_factory_installed = True
