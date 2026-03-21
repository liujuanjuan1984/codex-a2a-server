from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from a2a.server.agent_execution import RequestContext

from codex_a2a_server.contracts.runtime_output import SHARED_METADATA_NAMESPACE


def extract_namespaced_string_metadata(
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
        message_metadata = getattr(context.message, "metadata", None) or {}
        if isinstance(message_metadata, Mapping):
            candidates.append(message_metadata)

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


def extract_shared_session_id(context: RequestContext) -> str | None:
    return extract_namespaced_string_metadata(
        context,
        namespace=SHARED_METADATA_NAMESPACE,
        path=("session", "id"),
    )


def extract_codex_directory(context: RequestContext) -> str | None:
    return extract_namespaced_string_metadata(
        context,
        namespace="codex",
        path=("directory",),
    )
