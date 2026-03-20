from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shlex
import shutil
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .config import Settings
from .logging_context import bind_correlation_id, get_correlation_id, install_log_record_factory
from .tool_call_payloads import (
    ToolCallSourceMethod,
    as_tool_call_payload,
    tool_call_output_delta_payload_from_notification,
    tool_call_state_payload_from_item,
)

logger = logging.getLogger(__name__)


class _UnsetType:
    pass


_UNSET = _UnsetType()
_DEFAULT_CLIENT_NAME = "codex_a2a_server"
_DEFAULT_CLIENT_TITLE = "Codex A2A Server"
_EVENT_QUEUE_MAXSIZE = 2048


class CodexStartupPrerequisiteError(RuntimeError):
    """Raised when local Codex prerequisites are not satisfied."""


def _normalized_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _normalized_string(payload.get(key))
        if value is not None:
            return value
    return None


def _mapping_value(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _first_nested_string(payload: Mapping[str, Any], *paths: tuple[str, ...]) -> str | None:
    for path in paths:
        current: Any = payload
        for key in path:
            if not isinstance(current, Mapping):
                break
            current = current.get(key)
        else:
            value = _normalized_string(current)
            if value is not None:
                return value
    return None


def _extract_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = _normalized_string(item)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        values.append(normalized)
    return values


def _extract_permission_patterns(params: dict[str, Any]) -> list[str]:
    patterns = _extract_string_list(params.get("patterns"))
    if patterns:
        return patterns

    parsed_cmd = params.get("parsedCmd")
    if not isinstance(parsed_cmd, list):
        return []

    resolved_patterns: list[str] = []
    seen: set[str] = set()
    for entry in parsed_cmd:
        if not isinstance(entry, Mapping):
            continue
        path = _normalized_string(entry.get("path"))
        if path is None or path in seen:
            continue
        seen.add(path)
        resolved_patterns.append(path)
    return resolved_patterns


def _extract_question_properties_questions(params: dict[str, Any]) -> list[Any]:
    questions = params.get("questions")
    if isinstance(questions, list):
        return questions

    context = _mapping_value(params.get("context"))
    if context is not None and isinstance(context.get("questions"), list):
        return context["questions"]
    return []


def _build_codex_permission_interrupt_properties(
    *, request_key: str, session_id: str, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "id": request_key,
        "sessionID": session_id,
        "metadata": {"method": method, "raw": params},
    }
    display_message = _first_nested_string(
        params,
        ("request", "description"),
        ("description",),
        ("reason",),
        ("request", "reason"),
    )
    if display_message is not None:
        properties["display_message"] = display_message
    patterns = _extract_permission_patterns(params)
    if patterns:
        properties["patterns"] = patterns
    always = _extract_string_list(params.get("always"))
    if always:
        properties["always"] = always
    return properties


def _build_codex_question_interrupt_properties(
    *, request_key: str, session_id: str, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    properties = {
        "id": request_key,
        "sessionID": session_id,
        "questions": _extract_question_properties_questions(params),
        "metadata": {"method": method, "raw": params},
    }
    display_message = _first_nested_string(
        params,
        ("description",),
        ("context", "description"),
        ("prompt",),
    )
    if display_message is not None:
        properties["display_message"] = display_message
    return properties


def _extract_tool_status(payload: dict[str, Any]) -> str | None:
    value = _first_string(payload, "status")
    if value is not None:
        return value
    state = payload.get("state")
    if isinstance(state, dict):
        return _first_string(state, "status")
    return None


def _tool_source_method(method: str) -> ToolCallSourceMethod | None:
    parts = method.split("/")
    if len(parts) < 2:
        return None
    normalized = _normalized_string(parts[1])
    if normalized == "commandExecution":
        return "commandExecution"
    if normalized == "fileChange":
        return "fileChange"
    return None


def _build_tool_call_output_event(method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    thread_id = _first_string(params, "threadId")
    delta = params.get("delta")
    if thread_id is None or not isinstance(delta, str) or delta == "":
        return None

    explicit_call_id = _first_string(params, "callID", "callId", "call_id")
    item_id = _first_string(params, "itemId")
    call_id = explicit_call_id or item_id
    part_id = item_id or call_id
    if part_id is None:
        return None

    tool = _first_string(params, "tool", "name")
    status = _extract_tool_status(params)
    source_method = _tool_source_method(method)
    if source_method is None:
        return None
    payload = tool_call_output_delta_payload_from_notification(
        source_method=source_method,
        delta=delta,
        call_id=call_id,
        tool=tool,
        status=status,
    )
    if payload is None:
        return None

    part: dict[str, Any] = {
        "sessionID": thread_id,
        "id": part_id,
        "type": "tool_call",
        "role": "assistant",
    }
    if item_id is not None:
        part["messageID"] = item_id
    if call_id is not None:
        part["callID"] = call_id
    if tool is not None:
        part["tool"] = tool
    if status is not None:
        part["state"] = {"status": status}
    if source_method is not None:
        part["sourceMethod"] = source_method

    return {
        "type": "message.part.updated",
        "properties": {
            "part": part,
            "delta": as_tool_call_payload(payload),
        },
    }


def _build_tool_call_state_event(params: dict[str, Any]) -> dict[str, Any] | None:
    thread_id = _first_string(params, "threadId")
    item = params.get("item")
    if thread_id is None or not isinstance(item, dict):
        return None

    payload = tool_call_state_payload_from_item(item)
    if payload is None:
        return None

    part_id = _first_string(item, "id")
    if part_id is None:
        return None

    payload_data = as_tool_call_payload(payload)
    state_payload: dict[str, Any] = {}
    for key in ("status", "title", "subtitle", "input", "output", "error"):
        value = payload_data.get(key)
        if value is not None:
            state_payload[key] = value

    part: dict[str, Any] = {
        "sessionID": thread_id,
        "messageID": part_id,
        "id": part_id,
        "type": "tool_call",
        "role": "assistant",
    }
    call_id = payload_data.get("call_id")
    if isinstance(call_id, str) and call_id:
        part["callID"] = call_id
    tool = payload_data.get("tool")
    if isinstance(tool, str) and tool:
        part["tool"] = tool
    source_method = payload_data.get("source_method")
    if isinstance(source_method, str) and source_method:
        part["sourceMethod"] = source_method
    if state_payload:
        part["state"] = state_payload

    return {
        "type": "message.part.updated",
        "properties": {
            "part": part,
            "delta": payload_data,
        },
    }


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


class InterruptRequestError(RuntimeError):
    def __init__(
        self,
        *,
        error_type: str,
        request_id: str,
        expected_interrupt_type: str | None = None,
        actual_interrupt_type: str | None = None,
    ) -> None:
        super().__init__(error_type)
        self.error_type = error_type
        self.request_id = request_id
        self.expected_interrupt_type = expected_interrupt_type
        self.actual_interrupt_type = actual_interrupt_type


@dataclass(frozen=True)
class InterruptRequestBinding:
    request_id: str
    interrupt_type: str
    session_id: str
    created_at: float


@dataclass
class _PendingInterruptRequest:
    binding: InterruptRequestBinding
    rpc_request_id: str | int
    params: dict[str, Any]


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


class CodexClient:
    """Codex app-server client adapter (stdio JSON-RPC)."""

    def __init__(self, settings: Settings) -> None:
        install_log_record_factory()
        self._settings = settings
        self._workspace_root = settings.codex_workspace_root
        self._model_id = settings.codex_model_id
        self._stream_timeout = settings.codex_timeout_stream
        self._request_timeout = settings.codex_timeout
        self._cli_bin = settings.codex_cli_bin
        self._listen = settings.codex_app_server_listen
        self._default_model = settings.codex_model
        self._model_reasoning_effort = settings.codex_model_reasoning_effort
        self._interrupt_request_ttl_seconds = settings.a2a_interrupt_request_ttl_seconds
        self._log_payloads = settings.a2a_log_payloads

        self._process: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._closed = False

        self._init_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

        self._initialized = False
        self._next_request_id = 1
        self._pending_requests: dict[str, _PendingRpcRequest] = {}
        self._pending_server_requests: dict[str, _PendingInterruptRequest] = {}
        self._event_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._turn_trackers: dict[tuple[str, str], _TurnTracker] = {}

    async def close(self) -> None:
        self._closed = True
        async with self._state_lock:
            process = self._process
            self._process = None

        for task in (self._stdout_task, self._stderr_task):
            if task:
                task.cancel()
        self._stdout_task = None
        self._stderr_task = None

        for pending in self._pending_requests.values():
            if not pending.future.done():
                pending.future.set_exception(RuntimeError("codex app-server closed"))
        self._pending_requests.clear()

        if process:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=1.5)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    @property
    def stream_timeout(self) -> float | None:
        return self._stream_timeout

    @property
    def directory(self) -> str | None:
        return self._workspace_root

    @property
    def settings(self) -> Settings:
        return self._settings

    def _query_params(self, directory: str | None = None) -> dict[str, str]:
        d = directory or self._workspace_root
        if not d:
            return {}
        return {"directory": d}

    def _merge_params(
        self, extra: dict[str, Any] | None, *, directory: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = dict(self._query_params(directory=directory))
        if not extra:
            return params
        for key, value in extra.items():
            if value is None:
                continue
            if key == "directory":
                continue
            params[key] = value if isinstance(value, str) else str(value)
        return params

    def _resolve_cli_bin(self) -> str:
        cli_bin = self._cli_bin.strip() or "codex"
        if os.path.sep in cli_bin or (os.path.altsep and os.path.altsep in cli_bin):
            expanded = os.path.expanduser(cli_bin)
            if not os.path.exists(expanded):
                raise CodexStartupPrerequisiteError(
                    f"Codex prerequisite not satisfied: CLI binary not found at "
                    f"{expanded!r}. Install Codex or set CODEX_CLI_BIN to a valid "
                    "executable."
                )
            if not os.access(expanded, os.X_OK):
                raise CodexStartupPrerequisiteError(
                    f"Codex prerequisite not satisfied: CLI binary at {expanded!r} "
                    "is not executable. Fix permissions or set CODEX_CLI_BIN to a "
                    "valid executable."
                )
            return expanded

        resolved = shutil.which(cli_bin)
        if resolved is None and cli_bin == "codex":
            npm_global_bin = os.path.expanduser("~/.npm-global/bin/codex")
            if os.path.exists(npm_global_bin) and os.access(npm_global_bin, os.X_OK):
                resolved = npm_global_bin
        if resolved is None:
            raise CodexStartupPrerequisiteError(
                f"Codex prerequisite not satisfied: {cli_bin!r} was not found on "
                "PATH. Install Codex and verify the `codex` CLI is available "
                "before starting codex-a2a-server."
            )
        return resolved

    async def startup_preflight(self) -> None:
        try:
            await self._ensure_started()
        except CodexStartupPrerequisiteError:
            raise
        except FileNotFoundError as exc:
            raise CodexStartupPrerequisiteError(
                "Codex prerequisite not satisfied: failed to execute the "
                "configured Codex CLI. Verify that Codex is installed and "
                "CODEX_CLI_BIN points to a valid executable."
            ) from exc
        except Exception as exc:
            raise CodexStartupPrerequisiteError(
                "Codex prerequisite not satisfied: failed to start or initialize "
                "`codex app-server`. Verify that Codex itself is usable and "
                "that its provider/auth configuration is valid before "
                "starting codex-a2a-server."
            ) from exc

    async def _ensure_started(self) -> None:
        if self._closed:
            raise RuntimeError("codex client already closed")
        if self._initialized and self._process and self._process.returncode is None:
            return

        async with self._init_lock:
            if self._initialized and self._process and self._process.returncode is None:
                return
            if self._closed:
                raise RuntimeError("codex client already closed")

            cli_args: list[str] = [self._resolve_cli_bin()]
            if self._model_reasoning_effort:
                cli_args.extend(
                    [
                        "-c",
                        f"model_reasoning_effort={json.dumps(self._model_reasoning_effort)}",
                    ]
                )
            cli_args.extend(
                [
                    "app-server",
                    "--listen",
                    self._listen,
                ]
            )

            process = await asyncio.create_subprocess_exec(
                *cli_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            self._process = process
            self._stdout_task = asyncio.create_task(self._read_stdout_loop())
            self._stderr_task = asyncio.create_task(self._read_stderr_loop())

            init_result = await self._rpc_request(
                "initialize",
                {
                    "clientInfo": {
                        "name": _DEFAULT_CLIENT_NAME,
                        "title": _DEFAULT_CLIENT_TITLE,
                        "version": __version__,
                    },
                    "capabilities": {
                        "experimentalApi": True,
                    },
                },
                _skip_ensure=True,
            )
            if self._log_payloads:
                logger.debug("codex initialize result=%s", init_result)
            await self._send_json_message({"method": "initialized", "params": {}})
            self._initialized = True

    async def _read_stdout_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            async for line in self._iter_stream_lines(process.stdout):
                raw = line.decode("utf-8", errors="replace").strip()
                if not raw:
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("drop non-json line from codex app-server: %s", raw)
                    continue
                if not isinstance(message, dict):
                    logger.debug(
                        "drop non-object jsonrpc payload from codex app-server: %s",
                        type(message).__name__,
                    )
                    continue
                await self._dispatch_message(message)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("codex app-server stdout loop failed")
        finally:
            # Fail in-flight RPC futures if the process exits unexpectedly.
            for pending in self._pending_requests.values():
                if not pending.future.done():
                    pending.future.set_exception(RuntimeError("codex app-server stdout closed"))
            self._pending_requests.clear()

    async def _read_stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            async for line in self._iter_stream_lines(process.stderr):
                raw = line.decode("utf-8", errors="replace").rstrip()
                if raw:
                    logger.debug("codex app-server stderr: %s", raw)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("codex app-server stderr loop failed")

    async def _iter_stream_lines(
        self,
        stream: Any,
        *,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        buffer = bytearray()
        while True:
            chunk = await stream.read(chunk_size)
            if not chunk:
                break
            buffer.extend(chunk)
            while True:
                newline_index = buffer.find(b"\n")
                if newline_index < 0:
                    break
                line = bytes(buffer[:newline_index])
                del buffer[: newline_index + 1]
                yield line
        if buffer:
            yield bytes(buffer)

    async def _dispatch_message(self, message: dict[str, Any]) -> None:
        # 1) Server response to a client request.
        if "id" in message and ("result" in message or "error" in message):
            key = str(message["id"])
            pending = self._pending_requests.pop(key, None)
            if not pending:
                return
            with bind_correlation_id(pending.correlation_id):
                if "error" in message:
                    err = message["error"] if isinstance(message["error"], dict) else {}
                    code = int(err.get("code", -32000))
                    text = str(err.get("message", "unknown codex rpc error"))
                    logger.warning(
                        "codex rpc error method=%s request_id=%s code=%s",
                        pending.method,
                        pending.request_id,
                        code,
                    )
                    pending.future.set_exception(
                        CodexRPCError(code=code, message=text, data=err.get("data"))
                    )
                else:
                    logger.debug(
                        "codex rpc response method=%s request_id=%s",
                        pending.method,
                        pending.request_id,
                    )
                    pending.future.set_result(message.get("result"))
            return

        # 2) Server-initiated request (contains id + method).
        if "id" in message and "method" in message:
            await self._handle_server_request(message)
            return

        # 3) Server notification.
        if "method" in message:
            await self._handle_notification(message)

    async def _send_json_message(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("codex app-server is not running")
        line = json.dumps(payload, ensure_ascii=False)
        if self._log_payloads:
            logger.debug("codex app-server -> %s", line)
        async with self._write_lock:
            process.stdin.write((line + "\n").encode("utf-8"))
            await process.stdin.drain()

    async def _rpc_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        _skip_ensure: bool = False,
    ) -> Any:
        if not _skip_ensure:
            await self._ensure_started()
        request_id = str(self._next_request_id)
        self._next_request_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        correlation_id = get_correlation_id()
        self._pending_requests[request_id] = _PendingRpcRequest(
            request_id=request_id,
            method=method,
            future=future,
            correlation_id=correlation_id,
        )
        payload: dict[str, Any] = {"id": int(request_id), "method": method}
        if params is not None:
            payload["params"] = params
        logger.debug("codex rpc request method=%s request_id=%s", method, request_id)
        await self._send_json_message(payload)
        try:
            return await asyncio.wait_for(future, timeout=self._request_timeout)
        except TimeoutError as exc:
            pending = self._pending_requests.pop(request_id, None)
            with bind_correlation_id(correlation_id):
                logger.warning("codex rpc timeout method=%s request_id=%s", method, request_id)
            if pending is not None and not pending.future.done():
                pending.future.cancel()
            raise RuntimeError(f"codex rpc timeout: {method}") from exc

    async def _enqueue_stream_event(self, event: dict[str, Any]) -> None:
        if not self._event_subscribers:
            return
        for queue in tuple(self._event_subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Avoid backpressure deadlocks in degraded situations.
                logger.warning("codex event queue full; dropping oldest event")
                with contextlib.suppress(asyncio.QueueEmpty):
                    _ = queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    def _get_or_create_tracker(self, thread_id: str, turn_id: str) -> _TurnTracker:
        key = (thread_id, turn_id)
        tracker = self._turn_trackers.get(key)
        if tracker is None:
            tracker = _TurnTracker(thread_id=thread_id, turn_id=turn_id)
            self._turn_trackers[key] = tracker
        return tracker

    async def _handle_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str):
            return
        if not isinstance(params, dict):
            params = {}

        # v2 stream deltas -> normalized pseudo events consumed by agent.py
        if method == "item/agentMessage/delta":
            thread_id = str(params.get("threadId", "")).strip()
            turn_id = str(params.get("turnId", "")).strip()
            delta = params.get("delta")
            if thread_id and turn_id and isinstance(delta, str):
                tracker = self._get_or_create_tracker(thread_id, turn_id)
                tracker.text_chunks.append(delta)
                item_id = params.get("itemId")
                if isinstance(item_id, str) and item_id.strip():
                    tracker.message_id = item_id
                await self._enqueue_stream_event(
                    {
                        "type": "message.part.updated",
                        "properties": {
                            "part": {
                                "sessionID": thread_id,
                                "messageID": tracker.message_id or "",
                                "id": tracker.message_id or "",
                                "type": "text",
                                "role": "assistant",
                            },
                            "delta": delta,
                        },
                    }
                )
            return

        if method == "item/reasoning/summaryTextDelta":
            thread_id = str(params.get("threadId", "")).strip()
            delta = params.get("delta")
            item_id = str(params.get("itemId", "")).strip()
            if thread_id and isinstance(delta, str):
                await self._enqueue_stream_event(
                    {
                        "type": "message.part.updated",
                        "properties": {
                            "part": {
                                "sessionID": thread_id,
                                "messageID": item_id,
                                "id": item_id,
                                "type": "reasoning",
                                "role": "assistant",
                            },
                            "delta": delta,
                        },
                    }
                )
            return

        if method in {"item/started", "item/completed"}:
            event = _build_tool_call_state_event(params)
            if event is not None:
                await self._enqueue_stream_event(event)
            return

        if method in {"item/commandExecution/outputDelta", "item/fileChange/outputDelta"}:
            event = _build_tool_call_output_event(method, params)
            if event is not None:
                await self._enqueue_stream_event(event)
            return

        if method == "thread/tokenUsage/updated":
            thread_id = str(params.get("threadId", "")).strip()
            token_usage = params.get("tokenUsage")
            if not thread_id or not isinstance(token_usage, dict):
                return
            last = token_usage.get("last")
            if not isinstance(last, dict):
                return
            usage_event = {
                "type": "message.finalized",
                "properties": {
                    "sessionID": thread_id,
                    "info": {
                        "tokens": {
                            "input": last.get("inputTokens"),
                            "output": last.get("outputTokens"),
                            "total": last.get("totalTokens"),
                            "reasoning": last.get("reasoningOutputTokens"),
                            "cache": {"read": last.get("cachedInputTokens")},
                        }
                    },
                },
            }
            await self._enqueue_stream_event(usage_event)
            return

        if method == "turn/started":
            thread_id = str(params.get("threadId", "")).strip()
            turn = params.get("turn")
            if thread_id and isinstance(turn, dict):
                turn_id = str(turn.get("id", "")).strip()
                if turn_id:
                    self._get_or_create_tracker(thread_id, turn_id)
            return

        if method == "turn/completed":
            thread_id = str(params.get("threadId", "")).strip()
            turn = params.get("turn")
            if thread_id and isinstance(turn, dict):
                turn_id = str(turn.get("id", "")).strip()
                if turn_id:
                    tracker = self._get_or_create_tracker(thread_id, turn_id)
                    tracker.raw_turn = turn
                    status = str(turn.get("status", "")).strip()
                    if status.lower() in {"failed", "interrupted", "cancelled", "canceled"}:
                        error = turn.get("error")
                        if isinstance(error, dict):
                            error_message = error.get("message")
                            if isinstance(error_message, str) and error_message.strip():
                                tracker.error = error_message.strip()
                            else:
                                tracker.error = status or "turn failed"
                        else:
                            tracker.error = status or "turn failed"
                    tracker.completed.set()
            return

        if method == "error":
            # Optional mid-turn error notification, preserve for observability only.
            await self._enqueue_stream_event(
                {"type": "codex.error", "properties": {"payload": params}}
            )

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params")
        if not isinstance(method, str):
            return
        if params is None:
            params = {}
        if not isinstance(params, dict):
            params = {}

        request_key = str(request_id)

        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "applyPatchApproval",
            "execCommandApproval",
        }:
            session_id = str(params.get("threadId") or params.get("conversationId") or "").strip()
            rpc_request_id = request_id if isinstance(request_id, str | int) else request_key
            self._pending_server_requests[request_key] = _PendingInterruptRequest(
                binding=InterruptRequestBinding(
                    request_id=request_key,
                    interrupt_type="permission",
                    session_id=session_id,
                    created_at=time.monotonic(),
                ),
                rpc_request_id=rpc_request_id,
                params=params,
            )
            await self._enqueue_stream_event(
                {
                    "type": "permission.asked",
                    "properties": _build_codex_permission_interrupt_properties(
                        request_key=request_key,
                        session_id=session_id,
                        method=method,
                        params=params,
                    ),
                }
            )
            return

        if method == "item/tool/requestUserInput":
            session_id = str(params.get("threadId") or params.get("conversationId") or "").strip()
            rpc_request_id = request_id if isinstance(request_id, str | int) else request_key
            self._pending_server_requests[request_key] = _PendingInterruptRequest(
                binding=InterruptRequestBinding(
                    request_id=request_key,
                    interrupt_type="question",
                    session_id=session_id,
                    created_at=time.monotonic(),
                ),
                rpc_request_id=rpc_request_id,
                params=params,
            )
            await self._enqueue_stream_event(
                {
                    "type": "question.asked",
                    "properties": _build_codex_question_interrupt_properties(
                        request_key=request_key,
                        session_id=session_id,
                        method=method,
                        params=params,
                    ),
                }
            )
            return

        await self._send_json_message(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unsupported server request method: {method}",
                },
            }
        )

    async def stream_events(
        self, stop_event: asyncio.Event | None = None, *, directory: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        del directory
        await self._ensure_started()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_EVENT_QUEUE_MAXSIZE)
        self._event_subscribers.add(queue)
        try:
            while True:
                if stop_event and stop_event.is_set():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.25)
                except TimeoutError:
                    continue
                yield event
        finally:
            self._event_subscribers.discard(queue)

    async def create_session(
        self, title: str | None = None, *, directory: str | None = None
    ) -> str:
        del title
        params: dict[str, Any] = {}
        model = self._model_id or self._default_model
        if model:
            params["model"] = model
        if directory:
            params["cwd"] = directory
        elif self._workspace_root:
            params["cwd"] = self._workspace_root
        result = await self._rpc_request("thread/start", params)
        if not isinstance(result, dict):
            raise RuntimeError("codex thread/start response missing result object")
        thread = result.get("thread")
        if not isinstance(thread, dict):
            raise RuntimeError("codex thread/start response missing thread")
        session_id = thread.get("id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise RuntimeError("codex thread/start response missing thread id")
        return session_id.strip()

    async def list_sessions(self, *, params: dict[str, Any] | None = None) -> Any:
        query = self._merge_params(params)
        rpc_params: dict[str, Any] = {}
        if "limit" in query:
            with contextlib.suppress(ValueError):
                rpc_params["limit"] = int(query["limit"])
        result = await self._rpc_request("thread/list", rpc_params)
        if not isinstance(result, dict):
            return []
        data = result.get("data")
        if not isinstance(data, list):
            return []
        # Normalize to the shape expected by jsonrpc_ext list mapping.
        sessions: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            thread_id = item.get("id")
            if not isinstance(thread_id, str) or not thread_id.strip():
                continue
            sessions.append(
                {
                    "id": thread_id,
                    "title": item.get("preview") or thread_id,
                    "raw": item,
                }
            )
        return sessions

    async def list_messages(self, session_id: str, *, params: dict[str, Any] | None = None) -> Any:
        query = self._merge_params(params)
        limit: int | None = None
        if "limit" in query:
            with contextlib.suppress(ValueError):
                limit = int(query["limit"])
        result = await self._rpc_request(
            "thread/read",
            {"threadId": session_id, "includeTurns": True},
        )
        if not isinstance(result, dict):
            return []
        thread = result.get("thread")
        if not isinstance(thread, dict):
            return []
        turns = thread.get("turns")
        if not isinstance(turns, list):
            return []

        # Best-effort mapping into the legacy shape expected by jsonrpc_ext.
        messages: list[dict[str, Any]] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            items = turn.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", "")).lower()
                if item_type not in {"usermessage", "agentmessage"}:
                    continue
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    continue
                text = item.get("text")
                if not isinstance(text, str):
                    text = ""
                role = "assistant" if item_type == "agentmessage" else "user"
                messages.append(
                    {
                        "info": {"id": item_id, "role": role},
                        "parts": [{"type": "text", "text": text}],
                        "raw": item,
                    }
                )
        if limit is not None:
            messages = messages[-limit:]
        return messages

    async def send_message(
        self,
        session_id: str,
        text: str,
        *,
        directory: str | None = None,
        timeout_override: float | None | _UnsetType = _UNSET,
    ) -> CodexMessage:
        timeout_seconds: float | None
        if isinstance(timeout_override, _UnsetType):
            timeout_seconds = self._request_timeout
        elif timeout_override is None:
            timeout_seconds = None
        else:
            timeout_seconds = float(timeout_override)
            if timeout_seconds <= 0:
                timeout_seconds = self._request_timeout

        params: dict[str, Any] = {
            "threadId": session_id,
            "input": [{"type": "text", "text": text, "text_elements": []}],
        }
        if directory:
            params["cwd"] = directory
        elif self._workspace_root:
            params["cwd"] = self._workspace_root

        if self._model_id:
            params["model"] = self._model_id

        result = await self._rpc_request("turn/start", params)
        if not isinstance(result, dict):
            raise RuntimeError("codex turn/start response missing result object")
        turn = result.get("turn")
        if not isinstance(turn, dict):
            raise RuntimeError("codex turn/start response missing turn")
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise RuntimeError("codex turn/start response missing turn id")

        turn_id = turn_id.strip()
        tracker_key = (session_id, turn_id)
        tracker = self._get_or_create_tracker(session_id, turn_id)
        try:
            if timeout_seconds is None:
                await tracker.completed.wait()
            else:
                await asyncio.wait_for(tracker.completed.wait(), timeout=timeout_seconds)
            if tracker.error:
                raise RuntimeError(f"codex turn failed: {tracker.error}")
            return CodexMessage(
                text=tracker.text,
                session_id=session_id,
                message_id=tracker.message_id,
                raw={"turn": tracker.raw_turn or turn},
            )
        except TimeoutError as exc:
            raise RuntimeError("codex turn did not complete before timeout") from exc
        finally:
            # Completed/failed/timeout turns should not accumulate indefinitely.
            self._turn_trackers.pop(tracker_key, None)

    async def session_prompt_async(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "threadId": session_id,
            "input": _convert_request_parts_to_turn_input(request),
        }
        if directory:
            params["cwd"] = directory
        elif self._workspace_root:
            params["cwd"] = self._workspace_root
        if self._model_id:
            params["model"] = self._model_id
        result = await self._rpc_request("turn/start", params)
        if not isinstance(result, dict):
            raise RuntimeError("codex turn/start response missing result object")
        turn = result.get("turn")
        if not isinstance(turn, dict):
            raise RuntimeError("codex turn/start response missing turn")
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise RuntimeError("codex turn/start response missing turn id")
        return {"ok": True, "session_id": session_id, "turn_id": turn_id.strip()}

    async def session_command(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> CodexMessage:
        command = str(request["command"]).strip()
        arguments = str(request.get("arguments", "")).strip()
        prompt = f"/{command}" if not arguments else f"/{command} {arguments}"
        return await self.send_message(session_id, prompt, directory=directory)

    async def session_shell(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> dict[str, Any]:
        command_text = str(request["command"]).strip()
        if not command_text:
            raise RuntimeError("shell command must not be empty")
        # Shell execution remains a standalone Codex command/exec call. session_id
        # is preserved here for ownership/attribution, not to bind upstream thread context.
        result = await self._rpc_request(
            "command/exec",
            _build_shell_exec_params(
                command=shlex.split(command_text),
                directory=directory,
                default_workspace_root=self._workspace_root,
            ),
        )
        if not isinstance(result, dict):
            raise RuntimeError("codex command/exec response missing result object")
        return {
            "info": {
                "id": f"shell:{session_id}:{uuid_like_suffix(command_text)}",
                "role": "assistant",
            },
            "parts": [
                {
                    "type": "text",
                    "text": _format_shell_response(result),
                }
            ],
            "raw": result,
        }

    def _interrupt_request_status(
        self,
        binding: InterruptRequestBinding,
    ) -> str:
        expires_at = binding.created_at + float(self._interrupt_request_ttl_seconds)
        if expires_at <= time.monotonic():
            return "expired"
        return "active"

    def resolve_interrupt_request(
        self, request_id: str
    ) -> tuple[str, InterruptRequestBinding | None]:
        request_key = request_id.strip()
        pending = self._pending_server_requests.get(request_key)
        if pending is None:
            return "missing", None
        status = self._interrupt_request_status(pending.binding)
        if status == "expired":
            self._pending_server_requests.pop(request_key, None)
            return status, pending.binding
        return status, pending.binding

    def discard_interrupt_request(self, request_id: str) -> None:
        self._pending_server_requests.pop(request_id.strip(), None)

    def _require_pending_interrupt_request(
        self,
        request_id: str,
        *,
        expected_interrupt_type: str,
    ) -> _PendingInterruptRequest:
        request_key = request_id.strip()
        status, binding = self.resolve_interrupt_request(request_key)
        if status == "missing":
            raise InterruptRequestError(
                error_type="INTERRUPT_REQUEST_NOT_FOUND",
                request_id=request_key,
            )
        if status == "expired" or binding is None:
            raise InterruptRequestError(
                error_type="INTERRUPT_REQUEST_EXPIRED",
                request_id=request_key,
            )
        if binding.interrupt_type != expected_interrupt_type:
            raise InterruptRequestError(
                error_type="INTERRUPT_TYPE_MISMATCH",
                request_id=request_key,
                expected_interrupt_type=expected_interrupt_type,
                actual_interrupt_type=binding.interrupt_type,
            )
        pending = self._pending_server_requests.get(request_key)
        if pending is None:
            raise InterruptRequestError(
                error_type="INTERRUPT_REQUEST_NOT_FOUND",
                request_id=request_key,
            )
        return pending

    async def _reply_to_server_request(
        self,
        *,
        request_id: str,
        pending: _PendingInterruptRequest,
        result: dict[str, Any],
    ) -> None:
        await self._send_json_message({"id": pending.rpc_request_id, "result": result})

        resolved_type = (
            "question.replied"
            if pending.binding.interrupt_type == "question"
            else "permission.replied"
        )
        await self._enqueue_stream_event(
            {
                "type": resolved_type,
                "properties": {
                    "id": request_id,
                    "requestID": request_id,
                    "sessionID": pending.binding.session_id,
                },
            }
        )
        self.discard_interrupt_request(request_id)

    async def permission_reply(
        self,
        request_id: str,
        *,
        reply: str,
        message: str | None = None,
        directory: str | None = None,
    ) -> bool:
        del message, directory
        normalized = (reply or "").strip().lower()
        pending = self._require_pending_interrupt_request(
            request_id,
            expected_interrupt_type="permission",
        )
        decision = "decline"
        if normalized == "once":
            decision = "accept"
        elif normalized == "always":
            decision = "acceptForSession"
        elif normalized in {"reject", "deny"}:
            decision = "decline"

        await self._reply_to_server_request(
            request_id=request_id,
            pending=pending,
            result={"decision": decision},
        )
        return True

    async def question_reply(
        self,
        request_id: str,
        *,
        answers: list[list[str]],
        directory: str | None = None,
    ) -> bool:
        del directory
        pending = self._require_pending_interrupt_request(
            request_id,
            expected_interrupt_type="question",
        )
        # requestUserInput expects a dict keyed by question id.
        questions = pending.params.get("questions")
        answer_map: dict[str, dict[str, list[str]]] = {}
        if isinstance(questions, list):
            for index, q in enumerate(questions):
                if not isinstance(q, dict):
                    continue
                qid = q.get("id")
                if not isinstance(qid, str) or not qid:
                    continue
                selected = answers[index] if index < len(answers) else []
                selected = [v for v in selected if isinstance(v, str)]
                answer_map[qid] = {"answers": selected}
        await self._reply_to_server_request(
            request_id=request_id,
            pending=pending,
            result={"answers": answer_map},
        )
        return True

    async def question_reject(
        self,
        request_id: str,
        *,
        directory: str | None = None,
    ) -> bool:
        del directory
        pending = self._require_pending_interrupt_request(
            request_id,
            expected_interrupt_type="question",
        )
        # For requestUserInput, an empty answers map acts as reject/abort.
        await self._send_json_message({"id": pending.rpc_request_id, "result": {"answers": {}}})
        await self._enqueue_stream_event(
            {
                "type": "question.rejected",
                "properties": {
                    "id": request_id,
                    "requestID": request_id,
                    "sessionID": pending.binding.session_id,
                },
            }
        )
        self.discard_interrupt_request(request_id)
        return True


def _convert_request_parts_to_turn_input(request: dict[str, Any]) -> list[dict[str, Any]]:
    parts = request.get("parts")
    if not isinstance(parts, list):
        raise RuntimeError("request.parts must be an array")
    converted: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            raise RuntimeError("request.parts items must be objects")
        part_type = part.get("type")
        if part_type != "text":
            raise RuntimeError("Only text request.parts are currently supported")
        text = part.get("text")
        if not isinstance(text, str):
            raise RuntimeError("request.parts[].text must be a string")
        converted.append({"type": "text", "text": text, "text_elements": []})
    return converted


def _format_shell_response(result: dict[str, Any]) -> str:
    exit_code = result.get("exitCode")
    stdout = result.get("stdout")
    stderr = result.get("stderr")
    lines: list[str] = [f"exit_code: {exit_code}"]
    if isinstance(stdout, str) and stdout:
        lines.append("stdout:")
        lines.append(stdout.rstrip())
    if isinstance(stderr, str) and stderr:
        lines.append("stderr:")
        lines.append(stderr.rstrip())
    return "\n".join(lines)


def _build_shell_exec_params(
    *,
    command: list[str],
    directory: str | None,
    default_workspace_root: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"command": command}
    if directory:
        params["cwd"] = directory
    elif default_workspace_root:
        params["cwd"] = default_workspace_root
    return params


def uuid_like_suffix(value: str) -> str:
    normalized = value.strip().replace(" ", "-")
    if not normalized:
        return "empty"
    return normalized[:32]
