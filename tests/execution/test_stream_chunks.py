import logging

from a2a.types import DataPart, TextPart

from codex_a2a_server.execution.stream_chunks import (
    delta_chunks,
    snapshot_chunks,
    tool_delta_chunks,
    upsert_stream_part_state,
)
from codex_a2a_server.execution.stream_state import BlockType, StreamPartState


def test_upsert_stream_part_state_creates_and_updates_existing_state() -> None:
    part_states: dict[str, StreamPartState] = {}

    created = upsert_stream_part_state(
        part_states=part_states,
        part_id="part-1",
        part={"type": "text"},
        props={},
        role="agent",
        message_id="msg-1",
    )

    assert created is not None
    assert created.block_type == BlockType.TEXT
    assert created.role == "agent"
    assert created.message_id == "msg-1"

    updated = upsert_stream_part_state(
        part_states=part_states,
        part_id="part-1",
        part={"type": "reasoning"},
        props={},
        role=None,
        message_id="msg-2",
    )

    assert updated is created
    assert updated.block_type == BlockType.REASONING
    assert updated.role == "agent"
    assert updated.message_id == "msg-2"


def test_snapshot_chunks_emits_prefix_delta_and_suppresses_non_prefix_rewrite(
    caplog,
) -> None:
    state = StreamPartState(
        part_id="part-1",
        block_type=BlockType.TEXT,
        message_id="msg-1",
        role="agent",
        buffer="hello",
        saw_delta=True,
    )

    chunks = snapshot_chunks(
        state=state,
        snapshot="hello world",
        message_id="msg-2",
        task_id="task-1",
        session_id="ses-1",
    )

    assert len(chunks) == 1
    assert isinstance(chunks[0].part, TextPart)
    assert chunks[0].part.text == " world"
    assert chunks[0].source == "part_text_diff"
    assert state.buffer == "hello world"
    assert state.message_id == "msg-2"

    with caplog.at_level(logging.WARNING):
        suppressed = snapshot_chunks(
            state=state,
            snapshot="rewritten",
            message_id=None,
            task_id="task-1",
            session_id="ses-1",
        )

    assert suppressed == []
    assert state.buffer == "rewritten"
    assert "Suppressing non-prefix snapshot rewrite" in caplog.text


def test_delta_chunks_appends_text_and_marks_state_as_delta() -> None:
    state = StreamPartState(
        part_id="part-1",
        block_type=BlockType.TEXT,
        message_id=None,
        role="agent",
    )

    chunks = delta_chunks(
        state=state,
        delta_text="answer",
        message_id="msg-1",
        source="delta_event",
    )

    assert len(chunks) == 1
    assert isinstance(chunks[0].part, TextPart)
    assert chunks[0].part.text == "answer"
    assert chunks[0].append is True
    assert state.buffer == "answer"
    assert state.saw_delta is True
    assert state.message_id == "msg-1"


def test_tool_delta_chunks_normalizes_payload_and_rejects_unstructured_payload(caplog) -> None:
    state = StreamPartState(
        part_id="part-tool",
        block_type=BlockType.TOOL_CALL,
        message_id="msg-1",
        role="agent",
    )

    chunks = tool_delta_chunks(
        state=state,
        delta_value={"kind": "state", "tool": "bash", "status": "running"},
        message_id="msg-2",
        source="delta",
        task_id="task-1",
        session_id="ses-1",
    )

    assert len(chunks) == 1
    assert isinstance(chunks[0].part, DataPart)
    assert chunks[0].part.data == {"kind": "state", "tool": "bash", "status": "running"}
    assert state.message_id == "msg-2"

    with caplog.at_level(logging.WARNING):
        rejected = tool_delta_chunks(
            state=state,
            delta_value="bad-payload",
            message_id=None,
            source="delta",
            task_id="task-1",
            session_id="ses-1",
        )

    assert rejected == []
    assert "Suppressing non-structured tool_call payload" in caplog.text
