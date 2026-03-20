from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from .codex_client import CodexClient
from .extension_contracts import SHARED_METADATA_NAMESPACE
from .output_mapping import (
    build_assistant_message,
    build_history,
    build_output_metadata,
    enqueue_artifact_update,
    extract_token_usage,
    merge_token_usage,
)
from .stream_state import BlockType, StreamOutputState, build_stream_artifact_metadata
from .streaming import consume_codex_stream

logger = logging.getLogger(__name__)


class _TTLCache:
    """Bounded TTL cache for hashable key -> string value.

    This is intentionally tiny and dependency-free. It provides best-effort cleanup:
    - Expired entries are removed on get/set.
    - When maxsize is exceeded, we evict expired entries first, then the earliest-expiring entries.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int,
        maxsize: int,
        now: Callable[[], float] = time.monotonic,
        refresh_on_get: bool = False,
    ) -> None:
        self._ttl_seconds = int(ttl_seconds)
        self._maxsize = int(maxsize)
        self._now = now
        self._refresh_on_get = bool(refresh_on_get)
        # value: (string_value, expires_at_monotonic)
        self._store: dict[object, tuple[str, float]] = {}

    def get(self, key: object) -> str | None:
        if self._ttl_seconds <= 0 or self._maxsize <= 0:
            return None
        item = self._store.get(key)
        if not item:
            return None
        value, expires_at = item
        now = self._now()
        if expires_at <= now:
            self._store.pop(key, None)
            return None
        if self._refresh_on_get:
            self._store[key] = (value, now + float(self._ttl_seconds))
        return value

    def set(self, key: object, value: str) -> None:
        if self._ttl_seconds <= 0 or self._maxsize <= 0:
            return
        now = self._now()
        expires_at = now + float(self._ttl_seconds)
        self._store[key] = (value, expires_at)
        self._evict_if_needed(now=now)

    def pop(self, key: object) -> None:
        self._store.pop(key, None)

    def _evict_if_needed(self, *, now: float) -> None:
        if len(self._store) <= self._maxsize:
            return
        # 1) Drop expired.
        expired = [k for k, (_, exp) in self._store.items() if exp <= now]
        for k in expired:
            self._store.pop(k, None)
        if len(self._store) <= self._maxsize:
            return
        # 2) Still too big: evict the least recently renewed entries first.
        overflow = len(self._store) - self._maxsize
        by_expiry = sorted(self._store.items(), key=lambda item: item[1][1])
        for k, _ in by_expiry[:overflow]:
            self._store.pop(k, None)


class CodexAgentExecutor(AgentExecutor):
    def __init__(
        self,
        client: CodexClient,
        *,
        streaming_enabled: bool,
        cancel_abort_timeout_seconds: float = 1.0,
        session_cache_ttl_seconds: int = 3600,
        session_cache_maxsize: int = 10_000,
        stream_idle_diagnostic_seconds: float | None = None,
    ) -> None:
        self._client = client
        self._streaming_enabled = streaming_enabled
        self._cancel_abort_timeout_seconds = float(cancel_abort_timeout_seconds)
        self._stream_idle_diagnostic_seconds = stream_idle_diagnostic_seconds
        self._sessions = _TTLCache(
            ttl_seconds=session_cache_ttl_seconds,
            maxsize=session_cache_maxsize,
        )
        self._session_owners = _TTLCache(
            ttl_seconds=session_cache_ttl_seconds,
            maxsize=session_cache_maxsize,
            refresh_on_get=True,
        )  # session_id -> identity
        self._pending_session_claims: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._inflight_session_creates: dict[tuple[str, str], asyncio.Task[str]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._running_requests: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._running_stop_events: dict[tuple[str, str], asyncio.Event] = {}
        self._running_identities: dict[tuple[str, str], str] = {}

    def _resolve_and_validate_directory(self, requested: str | None) -> str | None:
        """Normalizes and validates the directory parameter against workspace boundaries.

        Returns:
            The normalized absolute path string if valid.
        Raises:
            ValueError: If the path is outside the allowed workspace.
        """
        base_dir_str = self._client.directory or os.getcwd()
        base_path = Path(base_dir_str).resolve()

        if requested is not None and not isinstance(requested, str):
            raise ValueError("Directory must be a string path")

        requested = requested.strip() if requested else requested
        if not requested:
            return str(base_path)

        def _resolve_requested(path: str) -> Path:
            p = Path(path)
            if not p.is_absolute():
                p = base_path / p
            return p.resolve()

        # 1. Deny override if disabled in settings
        if not self._client.settings.a2a_allow_directory_override:
            # If requested matches normalized base, it's fine.
            requested_path = _resolve_requested(requested)
            if requested_path == base_path:
                return str(base_path)
            raise ValueError("Directory override is disabled by service configuration")

        # 2. Resolve requested path
        requested_path = _resolve_requested(requested)

        # 3. Boundary check: must be subpath of base_path
        try:
            requested_path.relative_to(base_path)
        except ValueError as err:
            raise ValueError(
                f"Directory {requested} is outside the allowed workspace {base_path}"
            ) from err

        return str(requested_path)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            await self._emit_error(
                event_queue,
                task_id=task_id or "unknown",
                context_id=context_id or "unknown",
                message="Missing task_id or context_id in request context",
                streaming_request=self._should_stream(context),
            )
            return

        call_context = context.call_context
        identity = (call_context.state.get("identity") if call_context else None) or "anonymous"

        streaming_request = self._should_stream(context)
        user_text = context.get_user_input().strip()
        bound_session_id = _extract_shared_session_id(context)

        # Directory validation
        metadata = context.metadata
        if metadata is not None and not isinstance(metadata, Mapping):
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message="Invalid metadata: expected an object/map.",
                streaming_request=streaming_request,
            )
            return
        requested_dir = _extract_codex_directory(context)

        try:
            directory = self._resolve_and_validate_directory(requested_dir)
        except ValueError as e:
            logger.warning("Directory validation failed: %s", e)
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message=str(e),
                streaming_request=streaming_request,
            )
            return

        if not user_text:
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message="Only text input is supported.",
                streaming_request=streaming_request,
            )
            return

        logger.debug(
            "Received message identity=%s task_id=%s context_id=%s streaming=%s text=%s",
            identity,
            task_id,
            context_id,
            streaming_request,
            user_text,
        )

        stream_artifact_id = f"{task_id}:stream"
        stream_state = StreamOutputState(
            user_text=user_text,
            stable_message_id=f"{task_id}:{context_id}:assistant",
            event_id_namespace=f"{task_id}:{context_id}:{stream_artifact_id}",
        )
        stop_event = asyncio.Event()
        stream_completion_event = asyncio.Event()
        stream_task: asyncio.Task[None] | None = None
        pending_preferred_claim = False
        session_lock: asyncio.Lock | None = None
        session_id = ""
        execution_key = (task_id, context_id)
        current_task = asyncio.current_task()
        if current_task is not None:
            async with self._lock:
                self._running_requests[execution_key] = current_task
                self._running_stop_events[execution_key] = stop_event
                self._running_identities[execution_key] = identity

        try:
            session_id, pending_preferred_claim = await self._get_or_create_session(
                identity,
                context_id,
                user_text,
                preferred_session_id=bound_session_id,
                directory=directory,
            )
            session_lock = await self._get_session_lock(session_id)
            await session_lock.acquire()

            if streaming_request:
                stream_task = asyncio.create_task(
                    consume_codex_stream(
                        client=self._client,
                        session_id=session_id,
                        task_id=task_id,
                        context_id=context_id,
                        artifact_id=stream_artifact_id,
                        stream_state=stream_state,
                        event_queue=event_queue,
                        stop_event=stop_event,
                        completion_event=stream_completion_event,
                        idle_diagnostic_seconds=self._stream_idle_diagnostic_seconds,
                        directory=directory,
                    )
                )

            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.working),
                    final=False,
                )
            )
            send_kwargs: dict[str, Any] = {"directory": directory}
            if streaming_request:
                send_kwargs["timeout_override"] = self._client.stream_timeout
            response = await self._client.send_message(
                session_id,
                user_text,
                **send_kwargs,
            )

            if pending_preferred_claim:
                await self._finalize_preferred_session_binding(
                    identity=identity,
                    context_id=context_id,
                    session_id=session_id,
                )
                pending_preferred_claim = False

            response_text = response.text or ""
            resolved_message_id = stream_state.resolve_message_id(response.message_id)
            resolved_token_usage = merge_token_usage(
                extract_token_usage(response.raw),
                stream_state.token_usage,
            )
            logger.debug(
                "Codex response task_id=%s session_id=%s message_id=%s text=%s",
                task_id,
                response.session_id,
                resolved_message_id,
                response_text,
            )
            if streaming_request:
                stream_completion_event.set()
                if stream_task:
                    await stream_task
                    stream_task = None
                if stream_state.should_emit_final_snapshot(response_text):
                    sequence = stream_state.next_sequence()
                    await enqueue_artifact_update(
                        event_queue=event_queue,
                        task_id=task_id,
                        context_id=context_id,
                        artifact_id=stream_artifact_id,
                        part=TextPart(text=response_text),
                        append=stream_state.emitted_stream_chunk,
                        last_chunk=True,
                        artifact_metadata=build_stream_artifact_metadata(
                            block_type=BlockType.TEXT,
                            source="final_snapshot",
                            message_id=resolved_message_id,
                            sequence=sequence,
                            event_id=stream_state.build_event_id(sequence),
                        ),
                    )
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        status=TaskStatus(
                            state=TaskState.input_required,
                        ),
                        final=True,
                        metadata=build_output_metadata(
                            session_id=response.session_id,
                            usage=resolved_token_usage,
                            stream={
                                "message_id": resolved_message_id,
                                "event_id": f"{stream_state.event_id_namespace}:status",
                                "source": "status",
                            },
                        ),
                    )
                )
            else:
                response_text = response_text or "(No text content returned by Codex.)"
                assistant_message = build_assistant_message(
                    task_id=task_id,
                    context_id=context_id,
                    text=response_text,
                    message_id=resolved_message_id,
                )
                artifact = Artifact(
                    artifact_id=str(uuid.uuid4()),
                    name="response",
                    parts=[Part(root=TextPart(text=response_text))],
                )
                history = build_history(context)
                task = Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.input_required),
                    history=history,
                    artifacts=[artifact],
                    metadata=build_output_metadata(
                        session_id=response.session_id,
                        usage=resolved_token_usage,
                    ),
                )
                # Attach the assistant message as the current status message.
                task.status.message = assistant_message
                await event_queue.enqueue_event(task)
        except Exception as exc:
            logger.exception("Codex request failed")
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message=f"Codex error: {exc}",
                streaming_request=streaming_request,
            )
        finally:
            if pending_preferred_claim and session_id:
                with suppress(Exception):
                    await self._release_preferred_session_claim(
                        identity=identity,
                        session_id=session_id,
                    )
            stop_event.set()
            if stream_task:
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task
            if session_lock and session_lock.locked():
                session_lock.release()
            async with self._lock:
                self._running_requests.pop(execution_key, None)
                self._running_stop_events.pop(execution_key, None)
                self._running_identities.pop(execution_key, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        try:
            if not task_id or not context_id:
                await self._emit_error(
                    event_queue,
                    task_id=task_id or "unknown",
                    context_id=context_id or "unknown",
                    message="Missing task_id or context_id in request context",
                    streaming_request=False,
                )
                return

            call_context = context.call_context
            identity = (call_context.state.get("identity") if call_context else None) or "anonymous"

            event = TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.canceled),
                final=True,
            )
            await event_queue.enqueue_event(event)

            execution_key = (task_id, context_id)
            async with self._lock:
                running_identity = self._running_identities.get(execution_key, identity)
                running_task = self._running_requests.get(execution_key)
                stop_event = self._running_stop_events.get(execution_key)
                self._sessions.pop((running_identity, context_id))
                inflight = self._inflight_session_creates.pop((running_identity, context_id), None)
            if stop_event:
                stop_event.set()
            if (
                running_task
                and running_task is not asyncio.current_task()
                and not running_task.done()
            ):
                running_task.cancel()
            waitables: list[asyncio.Task[Any]] = []
            if (
                running_task
                and running_task is not asyncio.current_task()
                and not running_task.done()
            ):
                waitables.append(running_task)
            if inflight:
                inflight.cancel()
                waitables.append(inflight)

            if waitables and self._cancel_abort_timeout_seconds > 0:
                done, pending = await asyncio.wait(
                    set(waitables),
                    timeout=self._cancel_abort_timeout_seconds,
                )
                for task in done:
                    with suppress(asyncio.CancelledError, Exception):
                        await task
                if pending:
                    logger.warning(
                        "Cancel abort timeout exceeded task_id=%s context_id=%s "
                        "abort_timeout_seconds=%.3f pending_tasks=%s",
                        task_id,
                        context_id,
                        self._cancel_abort_timeout_seconds,
                        len(pending),
                    )
            elif waitables:
                logger.info(
                    "Cancel abort wait skipped task_id=%s context_id=%s abort_timeout_seconds=%.3f",
                    task_id,
                    context_id,
                    self._cancel_abort_timeout_seconds,
                )
        except Exception as exc:
            logger.exception("Cancel failed")
            if task_id and context_id:
                with suppress(Exception):
                    await self._emit_error(
                        event_queue,
                        task_id=task_id,
                        context_id=context_id,
                        message=f"Cancel failed: {exc}",
                        streaming_request=False,
                    )

    async def _get_or_create_session(
        self,
        identity: str,
        context_id: str,
        title: str,
        *,
        preferred_session_id: str | None = None,
        directory: str | None = None,
    ) -> tuple[str, bool]:
        # Caller explicitly bound the request to a known Codex session.
        if preferred_session_id:
            async with self._lock:
                owner = self._session_owners.get(preferred_session_id)
                pending_owner = self._pending_session_claims.get(preferred_session_id)
                if owner and owner != identity:
                    logger.warning(
                        "Identity %s tried to hijack session %s owned by %s",
                        identity,
                        preferred_session_id,
                        owner,
                    )
                    raise PermissionError(f"Session {preferred_session_id} is not owned by you")

                if pending_owner and pending_owner != identity:
                    logger.warning(
                        "Identity %s tried to use session %s while pending owner is %s",
                        identity,
                        preferred_session_id,
                        pending_owner,
                    )
                    raise PermissionError(f"Session {preferred_session_id} is not owned by you")

                # Existing owner is trusted and can be bound immediately.
                if owner == identity:
                    self._sessions.set((identity, context_id), preferred_session_id)
                    return preferred_session_id, False

                # Unknown owner: reserve a temporary claim; finalize after upstream send succeeds.
                self._pending_session_claims[preferred_session_id] = identity
                return preferred_session_id, True

        task: asyncio.Task[str] | None = None
        cache_key = (identity, context_id)
        async with self._lock:
            existing = self._sessions.get(cache_key)
            if existing:
                return existing, False
            task = self._inflight_session_creates.get(cache_key)
            if task is None:
                task = asyncio.create_task(
                    self._client.create_session(title=title, directory=directory)
                )
                self._inflight_session_creates[cache_key] = task

        try:
            session_id = await task
        except Exception:
            async with self._lock:
                if self._inflight_session_creates.get(cache_key) is task:
                    self._inflight_session_creates.pop(cache_key, None)
            raise

        async with self._lock:
            # Session create finished; commit to cache and drop inflight marker.
            owner = self._session_owners.get(session_id)
            if owner and owner != identity:
                if self._inflight_session_creates.get(cache_key) is task:
                    self._inflight_session_creates.pop(cache_key, None)
                raise PermissionError(f"Session {session_id} is not owned by you")
            self._sessions.set(cache_key, session_id)
            if not owner:
                self._session_owners.set(session_id, identity)
            if self._inflight_session_creates.get(cache_key) is task:
                self._inflight_session_creates.pop(cache_key, None)
        return session_id, False

    async def _finalize_preferred_session_binding(
        self,
        *,
        identity: str,
        context_id: str,
        session_id: str,
    ) -> None:
        async with self._lock:
            owner = self._session_owners.get(session_id)
            pending_owner = self._pending_session_claims.get(session_id)
            if owner and owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")
            if pending_owner and pending_owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")

            self._session_owners.set(session_id, identity)
            self._sessions.set((identity, context_id), session_id)
            if self._pending_session_claims.get(session_id) == identity:
                self._pending_session_claims.pop(session_id, None)

    async def _release_preferred_session_claim(self, *, identity: str, session_id: str) -> None:
        async with self._lock:
            if self._pending_session_claims.get(session_id) == identity:
                self._pending_session_claims.pop(session_id, None)

    async def claim_session(self, *, identity: str, session_id: str) -> bool:
        async with self._lock:
            owner = self._session_owners.get(session_id)
            pending_owner = self._pending_session_claims.get(session_id)
            if owner and owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")
            if pending_owner and pending_owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")
            if owner == identity:
                return False
            self._pending_session_claims[session_id] = identity
            return True

    async def finalize_session_claim(self, *, identity: str, session_id: str) -> None:
        async with self._lock:
            owner = self._session_owners.get(session_id)
            pending_owner = self._pending_session_claims.get(session_id)
            if owner and owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")
            if pending_owner and pending_owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")
            self._session_owners.set(session_id, identity)
            if pending_owner == identity:
                self._pending_session_claims.pop(session_id, None)

    async def release_session_claim(self, *, identity: str, session_id: str) -> None:
        await self._release_preferred_session_claim(identity=identity, session_id=session_id)

    async def session_owner_matches(self, *, identity: str, session_id: str) -> bool | None:
        async with self._lock:
            owner = self._session_owners.get(session_id)
            if owner:
                return owner == identity
            pending_owner = self._pending_session_claims.get(session_id)
            if pending_owner:
                return pending_owner == identity
        return None

    def resolve_directory(self, requested: str | None) -> str | None:
        return self._resolve_and_validate_directory(requested)

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            return lock

    async def _emit_error(
        self,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        message: str,
        *,
        streaming_request: bool,
    ) -> None:
        error_message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.agent,
            parts=[Part(root=TextPart(text=message))],
            task_id=task_id,
            context_id=context_id,
        )
        if streaming_request:
            await enqueue_artifact_update(
                event_queue=event_queue,
                task_id=task_id,
                context_id=context_id,
                artifact_id=f"{task_id}:error",
                part=TextPart(text=message),
                append=False,
                last_chunk=True,
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.failed),
                    final=True,
                )
            )
            return
        task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.failed, message=error_message),
            history=[error_message],
        )
        await event_queue.enqueue_event(task)

    def _should_stream(self, context: RequestContext) -> bool:
        if not self._streaming_enabled:
            return False
        call_context = context.call_context
        if not call_context:
            return False
        if call_context.state.get("a2a_streaming_request"):
            return True
        # JSON-RPC transport sets method in call context state.
        method = call_context.state.get("method")
        return method == "message/stream"


def _extract_namespaced_string_metadata(
    context: RequestContext,
    *,
    namespace: str,
    path: tuple[str, ...],
) -> str | None:
    candidates: list[Mapping[str, Any]] = []
    try:
        meta = context.metadata
        if isinstance(meta, Mapping):
            candidates.append(meta)
    except Exception:
        pass

    if context.message is not None:
        msg_meta = getattr(context.message, "metadata", None) or {}
        if isinstance(msg_meta, Mapping):
            candidates.append(msg_meta)

    for candidate in candidates:
        current = candidate.get(namespace)
        for part in path[:-1]:
            if not isinstance(current, Mapping):
                current = None
                break
            current = current.get(part)
        if not isinstance(current, Mapping):
            continue
        value = current.get(path[-1])
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _extract_shared_session_id(context: RequestContext) -> str | None:
    return _extract_namespaced_string_metadata(
        context,
        namespace=SHARED_METADATA_NAMESPACE,
        path=("session", "id"),
    )


def _extract_codex_directory(context: RequestContext) -> str | None:
    return _extract_namespaced_string_metadata(
        context,
        namespace="codex",
        path=("directory",),
    )
