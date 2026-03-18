from __future__ import annotations

import asyncio
import logging

from a2a.server.events import EventConsumer
from a2a.server.request_handlers.default_request_handler import (
    TERMINAL_TASK_STATES,
    DefaultRequestHandler,
)
from a2a.types import InternalError, Task, TaskIdParams, TaskNotCancelableError, TaskNotFoundError
from a2a.utils.errors import ServerError

from .metrics import A2A_STREAM_ACTIVE, A2A_STREAM_REQUESTS_TOTAL, get_metrics_registry

logger = logging.getLogger(__name__)


class CodexRequestHandler(DefaultRequestHandler):
    """Harden request lifecycle behavior around cancel, subscribe, and disconnects."""

    _metrics = get_metrics_registry()

    async def on_cancel_task(
        self,
        params: TaskIdParams,
        context=None,
    ) -> Task | None:
        task = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

        # Repeated cancel of an already-canceled task is idempotent.
        if task.status.state.value == "canceled":
            return task

        if task.status.state in TERMINAL_TASK_STATES:
            raise ServerError(
                error=TaskNotCancelableError(
                    message=f"Task cannot be canceled - current state: {task.status.state}"
                )
            )

        try:
            return await super().on_cancel_task(params, context)
        except ServerError as exc:
            if isinstance(exc.error, TaskNotCancelableError):
                refreshed = await self.task_store.get(params.id, context)
                if refreshed and refreshed.status.state.value == "canceled":
                    return refreshed
            raise

    async def on_resubscribe_to_task(
        self,
        params: TaskIdParams,
        context=None,
    ):
        task = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

        # Terminal tasks replay once and close cleanly.
        if task.status.state in TERMINAL_TASK_STATES:
            yield task
            return

        async for event in super().on_resubscribe_to_task(params, context):
            yield event

    async def on_message_send_stream(self, params, context=None):
        (
            _task_manager,
            task_id,
            queue,
            result_aggregator,
            producer_task,
        ) = await self._setup_message_execution(params, context)
        self._metrics.inc_counter(A2A_STREAM_REQUESTS_TOTAL)
        self._metrics.inc_gauge(A2A_STREAM_ACTIVE)
        logger.debug("A2A stream request started task_id=%s", task_id)
        consumer = EventConsumer(queue)
        producer_task.add_done_callback(consumer.agent_task_callback)
        stream_completed = False

        try:
            async for event in result_aggregator.consume_and_emit(consumer):
                if isinstance(event, Task):
                    self._validate_task_id_match(task_id, event.id)
                await self._send_push_notification_if_needed(task_id, result_aggregator)
                yield event
            stream_completed = True
        except (asyncio.CancelledError, GeneratorExit):
            logger.warning("Client disconnected. Cancelling producer task %s", task_id)
            producer_task.cancel()
            await queue.close(immediate=True)
            raise
        finally:
            self._metrics.dec_gauge(A2A_STREAM_ACTIVE)
            logger.debug(
                "A2A stream request closed task_id=%s completed=%s",
                task_id,
                stream_completed,
            )
            cleanup_task = asyncio.create_task(self._cleanup_producer(producer_task, task_id))
            cleanup_task.set_name(f"cleanup_producer:{task_id}")
            self._track_background_task(cleanup_task)

    async def on_message_send(self, params, context=None):
        (
            _task_manager,
            task_id,
            queue,
            result_aggregator,
            producer_task,
        ) = await self._setup_message_execution(params, context)

        logger.debug("A2A message request started task_id=%s", task_id)
        consumer = EventConsumer(queue)
        producer_task.add_done_callback(consumer.agent_task_callback)

        blocking = True
        if params.configuration and params.configuration.blocking is False:
            blocking = False

        result = None
        interrupted_or_non_blocking = False
        continuation_task = None
        try:

            async def push_notification_callback() -> None:
                await self._send_push_notification_if_needed(task_id, result_aggregator)

            (
                result,
                interrupted_or_non_blocking,
                continuation_task,
            ) = await result_aggregator.consume_and_break_on_interrupt(
                consumer,
                blocking=blocking,
                event_callback=push_notification_callback,
            )
            if continuation_task is not None:
                continuation_task.set_name(f"continue_consuming:{task_id}")
                self._track_background_task(continuation_task)
        except Exception:
            logger.exception("Agent execution failed")
            raise
        finally:
            if interrupted_or_non_blocking:
                cleanup_task = asyncio.create_task(self._cleanup_producer(producer_task, task_id))
                cleanup_task.set_name(f"cleanup_producer:{task_id}")
                self._track_background_task(cleanup_task)
            else:
                try:
                    if asyncio.current_task() and asyncio.current_task().cancelled():
                        logger.warning(
                            "Client disconnected from message request. Cancelling task %s", task_id
                        )
                        producer_task.cancel()
                        await queue.close(immediate=True)

                    await asyncio.shield(self._cleanup_producer(producer_task, task_id))
                except asyncio.CancelledError:
                    pass

        if not result:
            raise ServerError(error=InternalError())

        if isinstance(result, Task):
            self._validate_task_id_match(task_id, result.id)
            if params.configuration:
                from a2a.utils.task import apply_history_length

                result = apply_history_length(result, params.configuration.history_length)

        await self._send_push_notification_if_needed(task_id, result_aggregator)
        logger.debug("A2A message request completed task_id=%s blocking=%s", task_id, blocking)

        return result
