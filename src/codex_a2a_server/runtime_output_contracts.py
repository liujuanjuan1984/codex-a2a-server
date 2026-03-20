from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from a2a._base import A2ABaseModel
from pydantic import ConfigDict

SHARED_METADATA_NAMESPACE = "shared"

StreamBlockType = Literal["text", "reasoning", "tool_call"]


class _StrictContractModel(A2ABaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionMetadata(_StrictContractModel):
    id: str
    title: str | None = None


class ArtifactStreamMetadata(_StrictContractModel):
    block_type: StreamBlockType
    source: str
    message_id: str | None = None
    event_id: str | None = None
    sequence: int | None = None
    role: str | None = None


class StatusStreamMetadata(_StrictContractModel):
    source: str
    message_id: str | None = None
    event_id: str | None = None
    sequence: int | None = None


class InterruptMetadata(_StrictContractModel):
    request_id: str
    type: str
    phase: str
    resolution: str | None = None
    details: dict[str, Any] | None = None


class CacheTokensMetadata(_StrictContractModel):
    read_tokens: int | float | None = None
    write_tokens: int | float | None = None


class UsageMetadata(_StrictContractModel):
    input_tokens: int | float | None = None
    output_tokens: int | float | None = None
    total_tokens: int | float | None = None
    reasoning_tokens: int | float | None = None
    cache_tokens: CacheTokensMetadata | None = None
    cost: int | float | None = None
    raw: dict[str, Any] | None = None


class SharedOutputMetadata(_StrictContractModel):
    session: SessionMetadata | None = None
    usage: UsageMetadata | None = None
    stream: ArtifactStreamMetadata | StatusStreamMetadata | None = None
    interrupt: InterruptMetadata | None = None


def _model_dump_if_present(model: _StrictContractModel | None) -> dict[str, Any] | None:
    if model is None:
        return None
    payload = model.model_dump(mode="json", by_alias=False, exclude_none=True)
    return payload or None


def build_artifact_stream_metadata_payload(
    *,
    block_type: StreamBlockType,
    source: str,
    message_id: str | None = None,
    role: str | None = None,
    sequence: int | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    return ArtifactStreamMetadata(
        block_type=block_type,
        source=source,
        message_id=message_id,
        role=role,
        sequence=sequence,
        event_id=event_id,
    ).model_dump(mode="json", by_alias=False, exclude_none=True)


def build_status_stream_metadata(
    *,
    source: str,
    message_id: str | None = None,
    event_id: str | None = None,
    sequence: int | None = None,
) -> dict[str, Any]:
    return StatusStreamMetadata(
        source=source,
        message_id=message_id,
        event_id=event_id,
        sequence=sequence,
    ).model_dump(mode="json", by_alias=False, exclude_none=True)


def build_interrupt_metadata(
    *,
    request_id: str,
    interrupt_type: str,
    phase: str,
    details: Mapping[str, Any] | None = None,
    resolution: str | None = None,
) -> dict[str, Any]:
    normalized_details = dict(details) if details is not None else None
    return InterruptMetadata(
        request_id=request_id,
        type=interrupt_type,
        phase=phase,
        details=normalized_details,
        resolution=resolution,
    ).model_dump(mode="json", by_alias=False, exclude_none=True)


def build_output_metadata(
    *,
    session_id: str | None = None,
    session_title: str | None = None,
    usage: Mapping[str, Any] | None = None,
    stream: Mapping[str, Any] | None = None,
    interrupt: Mapping[str, Any] | None = None,
    codex_private: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    shared = SharedOutputMetadata(
        session=SessionMetadata(id=session_id, title=session_title) if session_id else None,
        usage=UsageMetadata.model_validate(dict(usage)) if usage is not None else None,
        stream=(
            ArtifactStreamMetadata.model_validate(dict(stream))
            if stream is not None and "block_type" in stream
            else (StatusStreamMetadata.model_validate(dict(stream)) if stream is not None else None)
        ),
        interrupt=InterruptMetadata.model_validate(dict(interrupt))
        if interrupt is not None
        else None,
    )
    metadata: dict[str, Any] = {}
    shared_payload = _model_dump_if_present(shared)
    if shared_payload:
        metadata[SHARED_METADATA_NAMESPACE] = shared_payload
    if codex_private:
        metadata["codex"] = dict(codex_private)
    return metadata or None


def build_stream_artifact_metadata(
    *,
    block_type: StreamBlockType,
    source: str,
    message_id: str | None = None,
    role: str | None = None,
    sequence: int | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    return {
        SHARED_METADATA_NAMESPACE: {
            "stream": build_artifact_stream_metadata_payload(
                block_type=block_type,
                source=source,
                message_id=message_id,
                role=role,
                sequence=sequence,
                event_id=event_id,
            )
        }
    }


def build_session_contract_params(*, field_path: str) -> dict[str, Any]:
    return {
        "required_fields": ["id"],
        "optional_fields": ["title"],
        "field_paths": {
            "id": f"{field_path}.id",
            "title": f"{field_path}.title",
        },
    }


def build_artifact_stream_contract_params(*, field_path: str) -> dict[str, Any]:
    return {
        "required_fields": ["block_type", "source"],
        "optional_fields": ["message_id", "event_id", "sequence", "role"],
        "field_paths": {
            "block_type": f"{field_path}.block_type",
            "source": f"{field_path}.source",
            "message_id": f"{field_path}.message_id",
            "event_id": f"{field_path}.event_id",
            "sequence": f"{field_path}.sequence",
            "role": f"{field_path}.role",
        },
    }


def build_status_stream_contract_params(*, field_path: str) -> dict[str, Any]:
    return {
        "required_fields": ["source"],
        "optional_fields": ["message_id", "event_id", "sequence"],
        "field_paths": {
            "source": f"{field_path}.source",
            "message_id": f"{field_path}.message_id",
            "event_id": f"{field_path}.event_id",
            "sequence": f"{field_path}.sequence",
        },
    }


def build_interrupt_contract_params(*, field_path: str) -> dict[str, Any]:
    return {
        "required_fields": ["request_id", "type", "phase"],
        "optional_fields": ["resolution", "details"],
        "open_object_fields": ["details"],
        "field_paths": {
            "request_id": f"{field_path}.request_id",
            "type": f"{field_path}.type",
            "phase": f"{field_path}.phase",
            "resolution": f"{field_path}.resolution",
            "details": f"{field_path}.details",
        },
    }


def build_usage_contract_params(*, field_path: str) -> dict[str, Any]:
    return {
        "required_fields": [],
        "optional_fields": [
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "reasoning_tokens",
            "cache_tokens",
            "cost",
            "raw",
        ],
        "open_object_fields": ["raw"],
        "nested_objects": {
            "cache_tokens": {
                "required_fields": [],
                "optional_fields": ["read_tokens", "write_tokens"],
            }
        },
        "field_paths": {
            "input_tokens": f"{field_path}.input_tokens",
            "output_tokens": f"{field_path}.output_tokens",
            "total_tokens": f"{field_path}.total_tokens",
            "reasoning_tokens": f"{field_path}.reasoning_tokens",
            "cache_read_tokens": f"{field_path}.cache_tokens.read_tokens",
            "cache_write_tokens": f"{field_path}.cache_tokens.write_tokens",
            "cost": f"{field_path}.cost",
            "raw": f"{field_path}.raw",
        },
    }
