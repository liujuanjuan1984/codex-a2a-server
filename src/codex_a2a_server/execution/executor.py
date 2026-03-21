from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from codex_a2a_server.codex_client import CodexClient
from codex_a2a_server.execution.cancellation import (
    await_cancel_cleanup,
    emit_canceled_status,
    prepare_cancel_waitables,
)
from codex_a2a_server.execution.directory_policy import resolve_and_validate_directory
from codex_a2a_server.execution.request_metadata import (
    extract_codex_directory,
    extract_shared_session_id,
)
from codex_a2a_server.execution.response_emitter import (
    emit_non_stream_completion,
    emit_streaming_completion,
)
from codex_a2a_server.execution.session_runtime import SessionRuntime
from codex_a2a_server.execution.stream_state import StreamOutputState
from codex_a2a_server.execution.streaming import consume_codex_stream

from .output_mapping import enqueue_artifact_update, extract_token_usage, merge_token_usage

logger = logging.getLogger(__name__)


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
        self._session_runtime = SessionRuntime(
            session_cache_ttl_seconds=session_cache_ttl_seconds,
            session_cache_maxsize=session_cache_maxsize,
        )
        self._sessions = self._session_runtime.session_bindings
        self._session_owners = self._session_runtime.session_owners
        self._pending_session_claims = self._session_runtime.pending_session_claims
        self._running_requests = self._session_runtime.running_requests
        self._running_stop_events = self._session_runtime.running_stop_events
        self._running_identities = self._session_runtime.running_identities

    def _resolve_and_validate_directory(self, requested: str | None) -> str | None:
        return resolve_and_validate_directory(self._client, requested)

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
        bound_session_id = extract_shared_session_id(context)

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
        requested_dir = extract_codex_directory(context)

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
        current_task = asyncio.current_task()
        if current_task is not None:
            await self._session_runtime.track_running_request(
                task_id=task_id,
                context_id=context_id,
                identity=identity,
                task=current_task,
                stop_event=stop_event,
            )

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
                await emit_streaming_completion(
                    event_queue=event_queue,
                    task_id=task_id,
                    context_id=context_id,
                    response_text=response_text,
                    session_id=response.session_id,
                    resolved_message_id=resolved_message_id,
                    resolved_token_usage=resolved_token_usage,
                    stream_artifact_id=stream_artifact_id,
                    stream_state=stream_state,
                )
            else:
                await emit_non_stream_completion(
                    event_queue=event_queue,
                    context=context,
                    task_id=task_id,
                    context_id=context_id,
                    response_text=response_text,
                    session_id=response.session_id,
                    resolved_message_id=resolved_message_id,
                    resolved_token_usage=resolved_token_usage,
                )
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
            await self._session_runtime.untrack_running_request(
                task_id=task_id,
                context_id=context_id,
            )

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

            await emit_canceled_status(
                event_queue,
                task_id=task_id,
                context_id=context_id,
            )

            running = await self._session_runtime.cancel_running_request(
                task_id=task_id,
                context_id=context_id,
                identity=identity,
            )
            waitables = prepare_cancel_waitables(running, current_task=asyncio.current_task())
            await await_cancel_cleanup(
                waitables,
                task_id=task_id,
                context_id=context_id,
                cancel_abort_timeout_seconds=self._cancel_abort_timeout_seconds,
                logger=logger,
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
        return await self._session_runtime.get_or_create_session(
            identity=identity,
            context_id=context_id,
            title=title,
            preferred_session_id=preferred_session_id,
            create_session=lambda: self._client.create_session(title=title, directory=directory),
        )

    async def _finalize_preferred_session_binding(
        self,
        *,
        identity: str,
        context_id: str,
        session_id: str,
    ) -> None:
        await self._session_runtime.finalize_preferred_session_binding(
            identity=identity,
            context_id=context_id,
            session_id=session_id,
        )

    async def _release_preferred_session_claim(self, *, identity: str, session_id: str) -> None:
        await self._session_runtime.release_preferred_session_claim(
            identity=identity,
            session_id=session_id,
        )

    async def claim_session(self, *, identity: str, session_id: str) -> bool:
        return await self._session_runtime.claim_session(
            identity=identity,
            session_id=session_id,
        )

    async def finalize_session_claim(self, *, identity: str, session_id: str) -> None:
        await self._session_runtime.finalize_session_claim(
            identity=identity,
            session_id=session_id,
        )

    async def release_session_claim(self, *, identity: str, session_id: str) -> None:
        await self._session_runtime.release_session_claim(
            identity=identity,
            session_id=session_id,
        )

    async def session_owner_matches(self, *, identity: str, session_id: str) -> bool | None:
        return await self._session_runtime.session_owner_matches(
            identity=identity,
            session_id=session_id,
        )

    def resolve_directory(self, requested: str | None) -> str | None:
        return self._resolve_and_validate_directory(requested)

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        return await self._session_runtime.get_session_lock(session_id)

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
