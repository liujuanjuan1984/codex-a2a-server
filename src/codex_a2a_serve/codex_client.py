from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .config import Settings

logger = logging.getLogger(__name__)

_UNSET = object()
_DEFAULT_CLIENT_NAME = "codex_a2a_serve"
_DEFAULT_CLIENT_TITLE = "Codex A2A Serve"
_DEFAULT_CLIENT_VERSION = "0.1.0"
_EVENT_QUEUE_MAXSIZE = 2048


@dataclass(frozen=True)
class OpencodeMessage:
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
class _PendingServerRequest:
    method: str
    request_id: str | int
    params: dict[str, Any]


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


class OpencodeClient:
    """Codex app-server client adapter (stdio JSON-RPC)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._directory = settings.codex_directory
        self._model_id = settings.codex_model_id
        self._stream_timeout = settings.codex_timeout_stream
        self._request_timeout = settings.codex_timeout
        self._cli_bin = settings.codex_cli_bin
        self._listen = settings.codex_app_server_listen
        self._default_model = settings.codex_model
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
        self._pending_requests: dict[str, asyncio.Future[Any]] = {}
        self._pending_server_requests: dict[str, _PendingServerRequest] = {}
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

        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(RuntimeError("codex app-server closed"))
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
        return self._directory

    @property
    def settings(self) -> Settings:
        return self._settings

    def _query_params(self, directory: str | None = None) -> dict[str, str]:
        d = directory or self._directory
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

            cli_bin = self._cli_bin
            if cli_bin == "codex" and shutil.which("codex") is None:
                npm_global_bin = os.path.expanduser("~/.npm-global/bin/codex")
                if os.path.exists(npm_global_bin):
                    cli_bin = npm_global_bin

            process = await asyncio.create_subprocess_exec(
                cli_bin,
                "app-server",
                "--listen",
                self._listen,
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
                        "version": _DEFAULT_CLIENT_VERSION,
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
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                raw = line.decode("utf-8", errors="replace").strip()
                if not raw:
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("drop non-json line from codex app-server: %s", raw)
                    continue
                await self._dispatch_message(message)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("codex app-server stdout loop failed")
        finally:
            # Fail in-flight RPC futures if the process exits unexpectedly.
            for future in self._pending_requests.values():
                if not future.done():
                    future.set_exception(RuntimeError("codex app-server stdout closed"))
            self._pending_requests.clear()

    async def _read_stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                raw = line.decode("utf-8", errors="replace").rstrip()
                if raw:
                    logger.debug("codex app-server stderr: %s", raw)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("codex app-server stderr loop failed")

    async def _dispatch_message(self, message: dict[str, Any]) -> None:
        # 1) Server response to a client request.
        if "id" in message and ("result" in message or "error" in message):
            key = str(message["id"])
            future = self._pending_requests.pop(key, None)
            if not future:
                return
            if "error" in message:
                err = message["error"] if isinstance(message["error"], dict) else {}
                code = int(err.get("code", -32000))
                text = str(err.get("message", "unknown codex rpc error"))
                future.set_exception(CodexRPCError(code=code, message=text, data=err.get("data")))
            else:
                future.set_result(message.get("result"))
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
        self._pending_requests[request_id] = future
        payload: dict[str, Any] = {"id": int(request_id), "method": method}
        if params is not None:
            payload["params"] = params
        await self._send_json_message(payload)
        try:
            return await asyncio.wait_for(future, timeout=self._request_timeout)
        except TimeoutError as exc:
            self._pending_requests.pop(request_id, None)
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

        if method in {"item/commandExecution/outputDelta", "item/fileChange/outputDelta"}:
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
                                "type": "tool_call",
                                "role": "assistant",
                            },
                            "delta": delta,
                        },
                    }
                )
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
                            message = error.get("message")
                            if isinstance(message, str) and message.strip():
                                tracker.error = message.strip()
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
            self._pending_server_requests[request_key] = _PendingServerRequest(
                method=method, request_id=request_id, params=params
            )
            session_id = str(params.get("threadId") or params.get("conversationId") or "").strip()
            await self._enqueue_stream_event(
                {
                    "type": "permission.asked",
                    "properties": {
                        "id": request_key,
                        "sessionID": session_id,
                        "permission": "approval",
                        "patterns": [],
                        "always": [],
                        "metadata": {"method": method, "raw": params},
                    },
                }
            )
            return

        if method == "item/tool/requestUserInput":
            self._pending_server_requests[request_key] = _PendingServerRequest(
                method=method, request_id=request_id, params=params
            )
            session_id = str(params.get("threadId") or "").strip()
            questions = params.get("questions")
            await self._enqueue_stream_event(
                {
                    "type": "question.asked",
                    "properties": {
                        "id": request_key,
                        "sessionID": session_id,
                        "questions": questions if isinstance(questions, list) else [],
                    },
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
        elif self._directory:
            params["cwd"] = self._directory
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
        del params
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
        return messages

    async def send_message(
        self,
        session_id: str,
        text: str,
        *,
        directory: str | None = None,
        timeout_override: float | None | object = _UNSET,
    ) -> OpencodeMessage:
        timeout_seconds: float | None
        if timeout_override is _UNSET:
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
        elif self._directory:
            params["cwd"] = self._directory

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
            return OpencodeMessage(
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

    async def _reply_to_server_request(self, request_id: str, result: dict[str, Any]) -> None:
        pending = self._pending_server_requests.get(request_id)
        if not pending:
            raise RuntimeError(f"interrupt request not found: {request_id}")
        await self._send_json_message({"id": pending.request_id, "result": result})

        session_id = str(
            pending.params.get("threadId") or pending.params.get("conversationId") or ""
        ).strip()
        resolved_type = (
            "question.replied"
            if pending.method == "item/tool/requestUserInput"
            else "permission.replied"
        )
        await self._enqueue_stream_event(
            {
                "type": resolved_type,
                "properties": {
                    "id": request_id,
                    "requestID": request_id,
                    "sessionID": session_id,
                },
            }
        )
        self._pending_server_requests.pop(request_id, None)

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
        decision = "decline"
        if normalized == "once":
            decision = "accept"
        elif normalized == "always":
            decision = "acceptForSession"
        elif normalized in {"reject", "deny"}:
            decision = "decline"

        await self._reply_to_server_request(request_id, {"decision": decision})
        return True

    async def question_reply(
        self,
        request_id: str,
        *,
        answers: list[list[str]],
        directory: str | None = None,
    ) -> bool:
        del directory
        # requestUserInput expects a dict keyed by question id.
        pending = self._pending_server_requests.get(request_id)
        if not pending:
            raise RuntimeError(f"interrupt request not found: {request_id}")
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
        await self._reply_to_server_request(request_id, {"answers": answer_map})
        return True

    async def question_reject(
        self,
        request_id: str,
        *,
        directory: str | None = None,
    ) -> bool:
        del directory
        pending = self._pending_server_requests.get(request_id)
        if not pending:
            raise RuntimeError(f"interrupt request not found: {request_id}")
        # For requestUserInput, an empty answers map acts as reject/abort.
        await self._send_json_message({"id": pending.request_id, "result": {"answers": {}}})
        session_id = str(pending.params.get("threadId") or "").strip()
        await self._enqueue_stream_event(
            {
                "type": "question.rejected",
                "properties": {
                    "id": request_id,
                    "requestID": request_id,
                    "sessionID": session_id,
                },
            }
        )
        self._pending_server_requests.pop(request_id, None)
        return True
