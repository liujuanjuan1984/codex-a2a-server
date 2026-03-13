import asyncio

import pytest
from a2a.types import TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent

from codex_a2a_server.agent import CodexAgentExecutor
from codex_a2a_server.codex_client import CodexMessage
from codex_a2a_server.streaming import extract_interrupt_resolved_event
from tests.helpers import (
    DummyEventQueue,
    make_request_context,
    make_settings,
    replay_codex_notification_fixture,
)


class DummyStreamingClient:
    def __init__(
        self,
        *,
        stream_events_payload: list[dict],
        stream_event_delays: list[float] | None = None,
        response_text: str,
        response_message_id: str | None = "msg-1",
        response_raw: dict | None = None,
        send_delay: float = 0.02,
    ) -> None:
        self._stream_events_payload = stream_events_payload
        self._stream_event_delays = stream_event_delays or [0.0] * len(stream_events_payload)
        self._response_text = response_text
        self._response_message_id = response_message_id
        self._response_raw = response_raw or {}
        self._send_delay = send_delay
        self._in_flight_send = 0
        self.max_in_flight_send = 0
        self.stream_timeout = None
        self.directory = None
        self._interrupt_sessions: dict[str, str] = {}
        self.settings = make_settings(
            a2a_bearer_token="test",
            codex_base_url="http://localhost",
        )

    async def create_session(
        self,
        title: str | None = None,
        *,
        directory: str | None = None,
    ) -> str:
        del title, directory
        return "ses-1"

    async def send_message(
        self,
        session_id: str,
        text: str,
        *,
        directory: str | None = None,
        timeout_override=None,  # noqa: ANN001
    ) -> CodexMessage:
        del text, directory, timeout_override
        self._in_flight_send += 1
        self.max_in_flight_send = max(self.max_in_flight_send, self._in_flight_send)
        await asyncio.sleep(self._send_delay)
        self._in_flight_send -= 1
        return CodexMessage(
            text=self._response_text,
            session_id=session_id,
            message_id=self._response_message_id,
            raw=self._response_raw,
        )

    async def stream_events(self, stop_event=None, *, directory: str | None = None):  # noqa: ANN001
        del directory
        for delay, event in zip(
            self._stream_event_delays, self._stream_events_payload, strict=False
        ):
            if stop_event and stop_event.is_set():
                break
            await asyncio.sleep(delay)
            yield event

    def remember_interrupt_request(self, *, request_id: str, session_id: str) -> None:
        self._interrupt_sessions[request_id] = session_id

    def resolve_interrupt_session(self, request_id: str) -> str | None:
        return self._interrupt_sessions.get(request_id)

    def discard_interrupt_request(self, request_id: str) -> None:
        self._interrupt_sessions.pop(request_id, None)


def _event(
    *,
    session_id: str,
    role: str | None,
    part_type: str,
    delta: str,
    message_id: str | None = "msg-1",
    part_id: str | None = None,
    text: str | None = None,
    part_overrides: dict | None = None,
) -> dict:
    resolved_part_id = part_id or f"prt-{message_id or 'missing'}-{part_type}"
    properties: dict = {
        "part": {
            "id": resolved_part_id,
            "sessionID": session_id,
            "type": part_type,
        },
        "delta": delta,
    }
    if role is not None:
        properties["part"]["role"] = role
    if message_id is not None:
        properties["part"]["messageID"] = message_id
    if text is not None:
        properties["part"]["text"] = text
    if part_overrides:
        properties["part"].update(part_overrides)
    return {
        "type": "message.part.updated",
        "properties": properties,
    }


def _delta_event(
    *,
    session_id: str,
    part_id: str,
    delta: str,
    message_id: str | None = "msg-1",
) -> dict:
    properties: dict = {
        "sessionID": session_id,
        "partID": part_id,
        "field": "text",
        "delta": delta,
    }
    if message_id is not None:
        properties["messageID"] = message_id
    return {
        "type": "message.part.delta",
        "properties": properties,
    }


def _tool_call_update_event(
    *,
    session_id: str,
    part_id: str,
    payload: dict,
    message_id: str | None = "msg-1",
    call_id: str | None = None,
    tool: str | None = None,
    source_method: str | None = None,
    status: str | None = None,
) -> dict:
    part: dict = {
        "id": part_id,
        "sessionID": session_id,
        "type": "tool_call",
        "role": "assistant",
    }
    if message_id is not None:
        part["messageID"] = message_id
    if call_id is not None:
        part["callID"] = call_id
    if tool is not None:
        part["tool"] = tool
    if source_method is not None:
        part["sourceMethod"] = source_method
    if status is not None:
        part["state"] = {"status": status}
    return {
        "type": "message.part.updated",
        "properties": {
            "part": part,
            "delta": payload,
        },
    }


def _fixture_response_message_id(fixture: dict) -> str:
    for notification in fixture["notifications"]:
        if notification.get("method") != "item/agentMessage/delta":
            continue
        params = notification.get("params", {})
        item_id = params.get("itemId")
        if isinstance(item_id, str) and item_id:
            return item_id
    raise AssertionError("fixture is missing item/agentMessage/delta")


def _fixture_session_id(fixture: dict) -> str:
    for notification in fixture["notifications"]:
        params = notification.get("params", {})
        thread_id = params.get("threadId")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    raise AssertionError("fixture is missing threadId")


def _step_finish_usage_event(
    *,
    session_id: str,
    message_id: str = "msg-1",
    part_id: str = "prt-step-finish",
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    cost: float,
) -> dict:
    return {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": part_id,
                "sessionID": session_id,
                "messageID": message_id,
                "type": "step-finish",
                "reason": "stop",
                "cost": cost,
                "tokens": {
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": total_tokens,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
            }
        },
    }


def _permission_asked_event(*, session_id: str, request_id: str) -> dict:
    return {
        "type": "permission.asked",
        "properties": {
            "id": request_id,
            "sessionID": session_id,
            "permission": "read",
            "patterns": ["/data/project/.env.secret"],
            "always": ["/data/project/.env.example"],
            "metadata": {"path": "/data/project/.env.secret"},
            "tool": {"messageID": "msg-tool-1", "callID": "call-tool-1"},
        },
    }


def _permission_replied_event(*, session_id: str, request_id: str) -> dict:
    return {
        "type": "permission.replied",
        "properties": {
            "id": request_id,
            "sessionID": session_id,
        },
    }


def _question_rejected_event(*, session_id: str, request_id: str) -> dict:
    return {
        "type": "question.rejected",
        "properties": {
            "id": request_id,
            "sessionID": session_id,
        },
    }


def _artifact_updates(queue: DummyEventQueue) -> list[TaskArtifactUpdateEvent]:
    return [event for event in queue.events if isinstance(event, TaskArtifactUpdateEvent)]


def _part_text(event: TaskArtifactUpdateEvent) -> str:
    part = event.artifact.parts[0]
    return getattr(part, "text", None) or getattr(getattr(part, "root", None), "text", "") or ""


def _part_data(event: TaskArtifactUpdateEvent) -> dict:
    part = event.artifact.parts[0]
    data = getattr(part, "data", None) or getattr(getattr(part, "root", None), "data", None)
    return data if isinstance(data, dict) else {}


def _part_kind(event: TaskArtifactUpdateEvent) -> str:
    part = event.artifact.parts[0]
    return getattr(part, "kind", None) or getattr(getattr(part, "root", None), "kind", "") or ""


def _artifact_stream_meta(event: TaskArtifactUpdateEvent) -> dict:
    return event.artifact.metadata["shared"]["stream"]


def _status_shared_meta(event: TaskStatusUpdateEvent) -> dict:
    return (event.metadata or {})["shared"]


def _interrupt_meta(event: TaskStatusUpdateEvent) -> dict:
    return _status_shared_meta(event)["interrupt"]


@pytest.mark.asyncio
async def test_streaming_filters_user_echo_and_emits_single_artifact_block_types() -> None:
    user_text = "who are you"
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="ROLE_USER", part_type="text", delta=user_text),
            _event(session_id="ses-1", role="assistant", part_type="reasoning", delta="thinking"),
            _tool_call_update_event(
                session_id="ses-1",
                part_id="prt-tool-search",
                tool="search",
                payload={
                    "kind": "state",
                    "tool": "search",
                },
            ),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="final answer"),
        ],
        response_text="final answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-1", context_id="ctx-1", text=user_text), queue
    )

    updates = _artifact_updates(queue)
    assert updates
    texts = [_part_text(event) for event in updates]
    assert user_text not in texts
    block_types = [_artifact_stream_meta(event)["block_type"] for event in updates]
    assert _unique(block_types) == ["reasoning", "tool_call", "text"]
    tool_updates = [
        event for event in updates if _artifact_stream_meta(event)["block_type"] == "tool_call"
    ]
    assert len(tool_updates) == 1
    assert _part_kind(tool_updates[0]) == "data"
    assert _part_data(tool_updates[0]) == {"kind": "state", "tool": "search"}
    artifact_ids = [event.artifact.artifact_id for event in updates]
    assert len(set(artifact_ids)) == 1
    sequences = [_artifact_stream_meta(event)["sequence"] for event in updates]
    assert sequences == list(range(1, len(updates) + 1))
    event_ids = [_artifact_stream_meta(event)["event_id"] for event in updates]
    assert event_ids == [f"task-1:ctx-1:task-1:stream:{seq}" for seq in sequences]


@pytest.mark.asyncio
async def test_streaming_does_not_send_duplicate_final_snapshot_when_chunks_exist() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                delta="stable final answer",
            ),
        ],
        response_text="stable final answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-2", context_id="ctx-2", text="hi"), queue
    )

    final_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "text"
    ]
    assert len(final_updates) == 1
    assert _part_text(final_updates[0]) == "stable final answer"
    assert _artifact_stream_meta(final_updates[0])["source"] != "final_snapshot"


@pytest.mark.asyncio
async def test_streaming_emits_final_snapshot_only_when_stream_has_no_final_answer() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="assistant", part_type="reasoning", delta="plan step"),
        ],
        response_text="final answer from send_message",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-3", context_id="ctx-3", text="hello"), queue
    )

    final_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "text"
    ]
    assert len(final_updates) == 1
    final_event = final_updates[0]
    assert _part_text(final_event) == "final answer from send_message"
    assert _artifact_stream_meta(final_event)["source"] == "final_snapshot"
    assert final_event.append is True
    assert final_event.last_chunk is True


@pytest.mark.asyncio
async def test_execute_serializes_send_message_per_session() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[],
        response_text="ok",
        send_delay=0.05,
    )
    executor = CodexAgentExecutor(client, streaming_enabled=False)
    queue_1 = DummyEventQueue()
    queue_2 = DummyEventQueue()
    metadata = {"shared": {"session": {"id": "ses-shared"}}}

    await asyncio.gather(
        executor.execute(
            make_request_context(
                task_id="task-4", context_id="ctx-4", text="hello", metadata=metadata
            ),
            queue_1,
        ),
        executor.execute(
            make_request_context(
                task_id="task-5", context_id="ctx-5", text="world", metadata=metadata
            ),
            queue_2,
        ),
    )

    assert client.max_in_flight_send == 1


@pytest.mark.asyncio
async def test_streaming_emits_events_without_message_id_using_stable_fallback() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                delta="stream chunk without id",
                message_id=None,
            ),
        ],
        response_text="stream chunk without id",
        response_message_id=None,
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-6", context_id="ctx-6", text="hello"), queue
    )

    updates = _artifact_updates(queue)
    assert len(updates) == 1
    update = updates[0]
    assert _part_text(update) == "stream chunk without id"
    assert _artifact_stream_meta(update)["source"] == "delta"
    assert _artifact_stream_meta(update)["block_type"] == "text"
    assert _artifact_stream_meta(update)["message_id"] == "task-6:ctx-6:assistant"
    assert _artifact_stream_meta(update)["event_id"] == "task-6:ctx-6:task-6:stream:1"
    final_status = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ][-1]
    assert _status_shared_meta(final_status)["stream"]["message_id"] == "task-6:ctx-6:assistant"
    assert (
        _status_shared_meta(final_status)["stream"]["event_id"]
        == "task-6:ctx-6:task-6:stream:status"
    )


@pytest.mark.asyncio
async def test_streaming_includes_usage_in_final_status_metadata() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="assistant", part_type="text", delta="answer"),
            _step_finish_usage_event(
                session_id="ses-1",
                input_tokens=12,
                output_tokens=4,
                total_tokens=16,
                cost=0.0012,
            ),
        ],
        response_text="answer",
        response_raw={
            "info": {
                "tokens": {
                    "input": 11,
                    "output": 5,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
                "cost": 0.0009,
            }
        },
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-usage", context_id="ctx-usage", text="hello"),
        queue,
    )

    final_status = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ][-1]
    usage = _status_shared_meta(final_status)["usage"]
    assert usage["input_tokens"] == 12
    assert usage["output_tokens"] == 4
    assert usage["total_tokens"] == 16
    assert usage["cost"] == 0.0012


@pytest.mark.asyncio
async def test_streaming_emits_interrupt_status_for_permission_asked_event() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _permission_asked_event(session_id="ses-1", request_id="perm-req-1"),
            _permission_asked_event(session_id="ses-1", request_id="perm-req-1"),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="answer"),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-perm", context_id="ctx-perm", text="hello"),
        queue,
    )

    interrupt_statuses = [
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.final is False
        and (event.metadata or {}).get("shared", {}).get("interrupt", {}).get("type")
        == "permission"
    ]
    assert len(interrupt_statuses) == 1
    interrupt = _interrupt_meta(interrupt_statuses[0])
    assert interrupt["request_id"] == "perm-req-1"
    assert interrupt["details"]["permission"] == "read"
    assert interrupt["phase"] == "asked"
    assert "resolution" not in interrupt
    assert "/data/project/.env.secret" in interrupt["details"]["patterns"]
    assert "metadata" not in interrupt["details"]
    assert "tool" not in interrupt["details"]
    assert (
        interrupt_statuses[0].metadata["codex"]["interrupt"]["metadata"]["path"]
        == "/data/project/.env.secret"
    )
    assert interrupt_statuses[0].status.state == TaskState.input_required


@pytest.mark.asyncio
async def test_streaming_emits_interrupt_resolved_status_once_per_pending_request() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _permission_asked_event(session_id="ses-1", request_id="perm-req-2"),
            _permission_replied_event(session_id="ses-1", request_id="perm-req-2"),
            _permission_replied_event(session_id="ses-1", request_id="perm-req-2"),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="answer"),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-perm-resolved", context_id="ctx-perm-resolved", text="hi"
        ),
        queue,
    )

    interrupt_statuses = [
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.final is False
        and (event.metadata or {}).get("shared", {}).get("interrupt", {}).get("request_id")
        == "perm-req-2"
    ]
    assert len(interrupt_statuses) == 2
    asked = _interrupt_meta(interrupt_statuses[0])
    resolved = _interrupt_meta(interrupt_statuses[1])
    assert asked["phase"] == "asked"
    assert interrupt_statuses[0].status.state == TaskState.input_required
    assert resolved["phase"] == "resolved"
    assert resolved["resolution"] == "replied"
    assert resolved["type"] == "permission"
    assert resolved["details"] == {}
    assert interrupt_statuses[1].status.state == TaskState.working


@pytest.mark.asyncio
async def test_streaming_emits_question_rejected_resolution_and_suppresses_unknown_resolved() -> (
    None
):
    client = DummyStreamingClient(
        stream_events_payload=[
            _question_rejected_event(session_id="ses-1", request_id="q-unknown"),
            {
                "type": "question.asked",
                "properties": {
                    "id": "q-1",
                    "sessionID": "ses-1",
                    "questions": [{"id": "q1", "question": "Continue?"}],
                },
            },
            _question_rejected_event(session_id="ses-1", request_id="q-1"),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="answer"),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-question-reject", context_id="ctx-question-reject", text="hi"
        ),
        queue,
    )

    question_interrupts = [
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.final is False
        and (event.metadata or {}).get("shared", {}).get("interrupt", {}).get("type") == "question"
    ]
    assert len(question_interrupts) == 2
    assert _interrupt_meta(question_interrupts[0])["phase"] == "asked"
    resolved = _interrupt_meta(question_interrupts[1])
    assert resolved["phase"] == "resolved"
    assert resolved["resolution"] == "rejected"
    assert resolved["request_id"] == "q-1"
    assert all(_interrupt_meta(event)["request_id"] != "q-unknown" for event in question_interrupts)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def test_extract_interrupt_resolved_event_accepts_request_id_aliases() -> None:
    legacy = extract_interrupt_resolved_event(
        {"type": "permission.replied", "properties": {"requestID": "perm-1"}}
    )
    modern = extract_interrupt_resolved_event(
        {"type": "permission.replied", "properties": {"id": "perm-2"}}
    )
    assert legacy == {
        "request_id": "perm-1",
        "event_type": "permission.replied",
        "interrupt_type": "permission",
        "resolution": "replied",
    }
    assert modern == {
        "request_id": "perm-2",
        "event_type": "permission.replied",
        "interrupt_type": "permission",
        "resolution": "replied",
    }


@pytest.mark.asyncio
async def test_streaming_treats_embedded_markers_as_plain_text_without_typed_parts() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="assistant", part_type="text", delta="start "),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="<thin"),
            _event(
                session_id="ses-1", role="assistant", part_type="text", delta="k>thinking</think> "
            ),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="middle "),
            _event(
                session_id="ses-1", role="assistant", part_type="text", delta='[tool_call: {"foo":'
            ),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="1}] end"),
        ],
        response_text='start <think>thinking</think> middle [tool_call: {"foo":1}] end',
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-embedded", context_id="ctx-embedded", text="go"), queue
    )

    updates = _artifact_updates(queue)

    def _final_state(block_type: str) -> str:
        parts = []
        for ev in updates:
            if _artifact_stream_meta(ev)["block_type"] == block_type:
                if not ev.append:
                    parts = [_part_text(ev)]
                else:
                    parts.append(_part_text(ev))
        return "".join(parts)

    assert _final_state("text") == 'start <think>thinking</think> middle [tool_call: {"foo":1}] end'
    assert _final_state("reasoning") == ""
    assert _final_state("tool_call") == ""


@pytest.mark.asyncio
async def test_streaming_emits_structured_tool_part_updates() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-1",
                part_overrides={
                    "callID": "call-1",
                    "tool": "bash",
                    "state": {"status": "pending"},
                },
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-1",
                part_overrides={
                    "callID": "call-1",
                    "tool": "bash",
                    "state": {"status": "running"},
                },
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-1",
                part_overrides={
                    "callID": "call-1",
                    "tool": "bash",
                    "state": {"status": "completed"},
                },
            ),
        ],
        response_text="done",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-tool-bracket", context_id="ctx-tool-bracket", text="go"),
        queue,
    )

    updates = _artifact_updates(queue)
    tool_updates = [ev for ev in updates if _artifact_stream_meta(ev)["block_type"] == "tool_call"]
    assert len(tool_updates) == 3
    assert all(_part_kind(ev) == "data" for ev in tool_updates)
    assert all(_part_data(ev)["kind"] == "state" for ev in tool_updates)
    assert [_part_data(ev)["status"] for ev in tool_updates] == ["pending", "running", "completed"]
    assert all(_part_data(ev)["tool"] == "bash" for ev in tool_updates)
    assert all(_part_data(ev)["call_id"] == "call-1" for ev in tool_updates)


@pytest.mark.asyncio
async def test_streaming_emits_non_json_tool_output_delta_payloads_as_data_parts() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _tool_call_update_event(
                session_id="ses-1",
                part_id="prt-tool-output",
                call_id="call-1",
                tool="bash",
                source_method="commandExecution",
                status="running",
                payload={
                    "kind": "output_delta",
                    "source_method": "commandExecution",
                    "call_id": "call-1",
                    "tool": "bash",
                    "status": "running",
                    "output_delta": (
                        "black...................................................................."
                    ),
                },
            ),
            _tool_call_update_event(
                session_id="ses-1",
                part_id="prt-tool-output",
                call_id="call-1",
                tool="bash",
                source_method="commandExecution",
                status="running",
                payload={
                    "kind": "output_delta",
                    "source_method": "commandExecution",
                    "call_id": "call-1",
                    "tool": "bash",
                    "status": "running",
                    "output_delta": "Passed\n",
                },
            ),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-tool-output", context_id="ctx-tool-output", text="go"),
        queue,
    )

    tool_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "tool_call"
    ]
    assert len(tool_updates) == 2
    assert all(_part_kind(event) == "data" for event in tool_updates)
    assert all(_part_data(event)["kind"] == "output_delta" for event in tool_updates)
    assert [_part_data(event)["output_delta"] for event in tool_updates] == [
        "black....................................................................",
        "Passed\n",
    ]
    assert all(_part_data(event)["tool"] == "bash" for event in tool_updates)
    assert all(_part_data(event)["call_id"] == "call-1" for event in tool_updates)
    assert all(_part_data(event)["source_method"] == "commandExecution" for event in tool_updates)


@pytest.mark.asyncio
async def test_streaming_preserves_repeated_identical_tool_output_deltas() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _tool_call_update_event(
                session_id="ses-1",
                part_id="prt-tool-repeat",
                call_id="call-1",
                tool="bash",
                source_method="commandExecution",
                status="running",
                payload={
                    "kind": "output_delta",
                    "source_method": "commandExecution",
                    "call_id": "call-1",
                    "tool": "bash",
                    "status": "running",
                    "output_delta": ".",
                },
            ),
            _tool_call_update_event(
                session_id="ses-1",
                part_id="prt-tool-repeat",
                call_id="call-1",
                tool="bash",
                source_method="commandExecution",
                status="running",
                payload={
                    "kind": "output_delta",
                    "source_method": "commandExecution",
                    "call_id": "call-1",
                    "tool": "bash",
                    "status": "running",
                    "output_delta": ".",
                },
            ),
            _tool_call_update_event(
                session_id="ses-1",
                part_id="prt-tool-repeat",
                call_id="call-1",
                tool="bash",
                source_method="commandExecution",
                status="running",
                payload={
                    "kind": "output_delta",
                    "source_method": "commandExecution",
                    "call_id": "call-1",
                    "tool": "bash",
                    "status": "running",
                    "output_delta": ".",
                },
            ),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-tool-repeat", context_id="ctx-tool-repeat", text="go"),
        queue,
    )

    tool_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "tool_call"
    ]
    assert len(tool_updates) == 3
    assert [_part_data(event)["output_delta"] for event in tool_updates] == [".", ".", "."]


@pytest.mark.asyncio
async def test_streaming_emits_file_change_output_delta_payloads_as_data_parts() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _tool_call_update_event(
                session_id="ses-1",
                part_id="prt-file-change",
                call_id="call-file-1",
                tool="apply_patch",
                source_method="fileChange",
                payload={
                    "kind": "output_delta",
                    "source_method": "fileChange",
                    "call_id": "call-file-1",
                    "tool": "apply_patch",
                    "output_delta": "Updated src/app.py\n",
                },
            ),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-file-change", context_id="ctx-file-change", text="go"),
        queue,
    )

    tool_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "tool_call"
    ]
    assert len(tool_updates) == 1
    assert _part_data(tool_updates[0]) == {
        "kind": "output_delta",
        "source_method": "fileChange",
        "call_id": "call-file-1",
        "tool": "apply_patch",
        "output_delta": "Updated src/app.py\n",
    }


@pytest.mark.asyncio
async def test_streaming_replays_real_command_execution_fixture_end_to_end() -> None:
    fixture, normalized_events = await replay_codex_notification_fixture(
        "codex_app_server",
        "command_execution_output_delta.json",
    )
    client = DummyStreamingClient(
        stream_events_payload=normalized_events,
        response_text=fixture["response_text"],
        response_message_id=_fixture_response_message_id(fixture),
    )
    fixture_session_id = _fixture_session_id(fixture)

    async def create_fixture_session(
        title: str | None = None,
        *,
        directory: str | None = None,
    ) -> str:
        del title, directory
        return fixture_session_id

    client.create_session = create_fixture_session  # type: ignore[method-assign]
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-tool-fixture-command",
            context_id="ctx-tool-fixture-command",
            text="go",
        ),
        queue,
    )

    tool_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "tool_call"
    ]
    assert len(tool_updates) == 4
    assert [_part_data(event)["kind"] for event in tool_updates] == [
        "state",
        "output_delta",
        "output_delta",
        "state",
    ]
    assert [
        _part_data(event)["status"] for event in tool_updates if "status" in _part_data(event)
    ] == [
        "running",
        "completed",
    ]
    assert [
        _part_data(event)["output_delta"]
        for event in tool_updates
        if _part_data(event)["kind"] == "output_delta"
    ] == [
        "chunk-1\n",
        "chunk-2\n",
    ]
    assert all(_part_data(event)["source_method"] == "commandExecution" for event in tool_updates)
    assert all(_part_data(event)["call_id"] == "call-fixture-command" for event in tool_updates)
    assert _part_data(tool_updates[-1])["output"] == {
        "text": "chunk-1\nchunk-2\n",
        "exit_code": 0,
        "duration_ms": 487,
    }


@pytest.mark.asyncio
async def test_streaming_replays_real_file_change_fixture_end_to_end() -> None:
    fixture, normalized_events = await replay_codex_notification_fixture(
        "codex_app_server",
        "file_change_output_delta.json",
    )
    client = DummyStreamingClient(
        stream_events_payload=normalized_events,
        response_text=fixture["response_text"],
        response_message_id=_fixture_response_message_id(fixture),
    )
    fixture_session_id = _fixture_session_id(fixture)

    async def create_fixture_session(
        title: str | None = None,
        *,
        directory: str | None = None,
    ) -> str:
        del title, directory
        return fixture_session_id

    client.create_session = create_fixture_session  # type: ignore[method-assign]
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-tool-fixture-file-change",
            context_id="ctx-tool-fixture-file-change",
            text="go",
        ),
        queue,
    )

    tool_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "tool_call"
    ]
    assert len(tool_updates) == 3
    assert [_part_data(event)["kind"] for event in tool_updates] == [
        "state",
        "output_delta",
        "state",
    ]
    assert _part_data(tool_updates[0]) == {
        "kind": "state",
        "source_method": "fileChange",
        "call_id": "call-fixture-file-change",
        "status": "running",
        "input": {
            "paths": ["/tmp/codex-a2a-file-change-fixture/fixture-from-codex.txt"],
            "change_count": 1,
        },
    }
    assert _part_data(tool_updates[1]) == {
        "kind": "output_delta",
        "source_method": "fileChange",
        "call_id": "call-fixture-file-change",
        "output_delta": "Success. Updated the following files:\nA fixture-from-codex.txt\n",
    }
    assert _part_data(tool_updates[2]) == {
        "kind": "state",
        "source_method": "fileChange",
        "call_id": "call-fixture-file-change",
        "status": "completed",
        "input": {
            "paths": ["/tmp/codex-a2a-file-change-fixture/fixture-from-codex.txt"],
            "change_count": 1,
        },
    }


@pytest.mark.asyncio
async def test_streaming_flushes_partial_marker_on_eof_as_current_block_type() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="assistant", part_type="text", delta="hello <thin"),
        ],
        response_text="hello <thin",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-eof-flush", context_id="ctx-eof-flush", text="go"),
        queue,
    )

    updates = _artifact_updates(queue)
    assert updates
    assert "".join(_part_text(ev) for ev in updates) == "hello <thin"
    assert all(_artifact_stream_meta(ev)["block_type"] == "text" for ev in updates)


@pytest.mark.asyncio
async def test_streaming_never_resets_single_artifact_after_first_chunk() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "id": "prt-no-reset-1",
                        "sessionID": "ses-1",
                        "type": "text",
                        "role": "assistant",
                        "messageID": "msg-1",
                        "text": "hello",
                    },
                    "delta": "",
                },
            },
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "id": "prt-no-reset-1",
                        "sessionID": "ses-1",
                        "type": "text",
                        "role": "assistant",
                        "messageID": "msg-1",
                        "text": "HELLO",
                    },
                    "delta": "",
                },
            },
        ],
        response_text="HELLO",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-no-reset", context_id="ctx-no-reset", text="go"),
        queue,
    )

    updates = _artifact_updates(queue)
    assert len(updates) >= 2
    assert updates[0].append is False
    assert all(ev.append is True for ev in updates[1:])


@pytest.mark.asyncio
async def test_streaming_suppresses_reasoning_snapshot_reset_after_delta() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="",
                part_id="prt-r1",
                text="",
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="reasoning line\n\n",
                part_id="prt-r1",
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="",
                part_id="prt-r1",
                text="reasoning line",
            ),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-reason-reset", context_id="ctx-reason-reset", text="go"),
        queue,
    )

    reasoning_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "reasoning"
    ]
    assert len(reasoning_updates) == 1
    assert _part_text(reasoning_updates[0]) == "reasoning line\n\n"


@pytest.mark.asyncio
async def test_streaming_supports_message_part_delta_events() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="",
                part_id="prt-r2",
                text="",
            ),
            _delta_event(session_id="ses-1", part_id="prt-r2", delta="first "),
            _delta_event(session_id="ses-1", part_id="prt-r2", delta="second"),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-delta", context_id="ctx-delta", text="go"),
        queue,
    )

    reasoning_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "reasoning"
    ]
    assert reasoning_updates
    merged = "".join(_part_text(ev) for ev in reasoning_updates)
    assert merged == "first second"


@pytest.mark.asyncio
async def test_streaming_aggregates_small_text_deltas_into_single_update() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                delta="",
                part_id="prt-text-agg",
                text="",
            ),
            _delta_event(session_id="ses-1", part_id="prt-text-agg", delta="你"),
            _delta_event(session_id="ses-1", part_id="prt-text-agg", delta="好"),
            _delta_event(session_id="ses-1", part_id="prt-text-agg", delta="，"),
            _delta_event(session_id="ses-1", part_id="prt-text-agg", delta="世"),
            _delta_event(session_id="ses-1", part_id="prt-text-agg", delta="界"),
        ],
        response_text="你好，世界",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-text-agg",
            context_id="ctx-text-agg",
            text="go",
        ),
        queue,
    )

    text_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "text"
    ]
    assert len(text_updates) == 1
    assert _part_text(text_updates[0]) == "你好，世界"


@pytest.mark.asyncio
async def test_streaming_emits_structured_tool_call_delta_payloads_as_data_parts() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _tool_call_update_event(
                session_id="ses-1",
                part_id="prt-tool-delta",
                call_id="call-1",
                tool="bash",
                status="running",
                payload={
                    "kind": "state",
                    "call_id": "call-1",
                    "tool": "bash",
                    "status": "running",
                },
            ),
            _tool_call_update_event(
                session_id="ses-1",
                part_id="prt-tool-delta",
                call_id="call-1",
                tool="bash",
                status="completed",
                payload={
                    "kind": "state",
                    "call_id": "call-1",
                    "tool": "bash",
                    "status": "completed",
                },
            ),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-tool-delta", context_id="ctx-tool-delta", text="go"),
        queue,
    )

    tool_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "tool_call"
    ]
    assert len(tool_updates) == 2
    assert all(_part_kind(event) == "data" for event in tool_updates)
    assert all(_part_data(event)["kind"] == "state" for event in tool_updates)
    assert [_part_data(event)["status"] for event in tool_updates] == ["running", "completed"]
    assert all(_part_data(event)["tool"] == "bash" for event in tool_updates)
    assert all(_part_data(event)["call_id"] == "call-1" for event in tool_updates)


@pytest.mark.asyncio
async def test_streaming_suppresses_legacy_string_tool_call_deltas() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool_call",
                delta="",
                part_id="prt-tool-legacy",
                text="",
            ),
            _delta_event(
                session_id="ses-1",
                part_id="prt-tool-legacy",
                delta='{"tool":"bash","status":"running"}',
            ),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-tool-legacy", context_id="ctx-tool-legacy", text="go"),
        queue,
    )

    tool_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "tool_call"
    ]
    assert tool_updates == []


@pytest.mark.asyncio
async def test_streaming_interleaves_tool_state_and_output_delta_updates() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-mixed",
                part_overrides={
                    "callID": "call-1",
                    "tool": "bash",
                    "state": {"status": "pending"},
                },
            ),
            _tool_call_update_event(
                session_id="ses-1",
                part_id="prt-tool-mixed",
                call_id="call-1",
                tool="bash",
                source_method="commandExecution",
                status="running",
                payload={
                    "kind": "output_delta",
                    "source_method": "commandExecution",
                    "call_id": "call-1",
                    "tool": "bash",
                    "status": "running",
                    "output_delta": "pytest ... ",
                },
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-mixed",
                part_overrides={
                    "callID": "call-1",
                    "tool": "bash",
                    "state": {"status": "running"},
                },
            ),
            _tool_call_update_event(
                session_id="ses-1",
                part_id="prt-tool-mixed",
                call_id="call-1",
                tool="bash",
                source_method="commandExecution",
                status="running",
                payload={
                    "kind": "output_delta",
                    "source_method": "commandExecution",
                    "call_id": "call-1",
                    "tool": "bash",
                    "status": "running",
                    "output_delta": "Passed\n",
                },
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-mixed",
                part_overrides={
                    "callID": "call-1",
                    "tool": "bash",
                    "state": {"status": "completed"},
                },
            ),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-tool-mixed", context_id="ctx-tool-mixed", text="go"),
        queue,
    )

    tool_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "tool_call"
    ]
    assert len(tool_updates) == 5
    assert [_part_data(event)["kind"] for event in tool_updates] == [
        "state",
        "output_delta",
        "state",
        "output_delta",
        "state",
    ]
    assert [
        _part_data(event).get("status")
        for event in tool_updates
        if _part_data(event)["kind"] == "state"
    ] == ["pending", "running", "completed"]
    assert [
        _part_data(event).get("output_delta")
        for event in tool_updates
        if _part_data(event)["kind"] == "output_delta"
    ] == ["pytest ... ", "Passed\n"]


@pytest.mark.asyncio
async def test_streaming_buffers_delta_until_part_updated_arrives() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _delta_event(session_id="ses-1", part_id="prt-late", delta="first "),
            _delta_event(session_id="ses-1", part_id="prt-late", delta="second"),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="",
                part_id="prt-late",
                text="first second",
            ),
        ],
        response_text="answer",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-buffered-delta", context_id="ctx-buffered-delta", text="go"
        ),
        queue,
    )

    reasoning_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "reasoning"
    ]
    assert reasoning_updates
    merged = "".join(_part_text(ev) for ev in reasoning_updates)
    assert merged == "first second"


@pytest.mark.asyncio
async def test_streaming_flushes_reasoning_buffer_after_time_threshold() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="",
                part_id="prt-reasoning-timer",
                text="",
            ),
            _delta_event(
                session_id="ses-1",
                part_id="prt-reasoning-timer",
                delta="a" * 120,
            ),
            _delta_event(
                session_id="ses-1",
                part_id="prt-reasoning-timer",
                delta="b" * 10,
            ),
        ],
        stream_event_delays=[0.0, 0.0, 0.4],
        response_text="answer",
        send_delay=0.6,
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-reasoning-timer",
            context_id="ctx-reasoning-timer",
            text="go",
        ),
        queue,
    )

    reasoning_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "reasoning"
    ]
    assert len(reasoning_updates) == 2
    assert _part_text(reasoning_updates[0]) == "a" * 120
    assert _part_text(reasoning_updates[1]) == "b" * 10


@pytest.mark.asyncio
async def test_streaming_keeps_multiple_message_ids_in_same_request_window() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                part_id="prt-m1",
                message_id="msg-a",
                delta="step one ",
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                part_id="prt-m2",
                message_id="msg-b",
                delta="final answer",
            ),
        ],
        response_text="final answer",
        response_message_id="msg-b",
    )
    executor = CodexAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-multi-mid", context_id="ctx-multi-mid", text="go"),
        queue,
    )

    updates = _artifact_updates(queue)
    message_ids = [_artifact_stream_meta(ev).get("message_id") for ev in updates]
    assert "msg-a" in message_ids
    assert "msg-b" in message_ids
