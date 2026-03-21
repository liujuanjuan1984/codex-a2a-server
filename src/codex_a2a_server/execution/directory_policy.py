from __future__ import annotations

import os
from pathlib import Path

from codex_a2a_server.upstream.client import CodexClient


def resolve_and_validate_directory(
    client: CodexClient,
    requested: str | None,
) -> str | None:
    """Normalize and validate the directory parameter against workspace boundaries."""
    base_dir_str = client.directory or os.getcwd()
    base_path = Path(base_dir_str).resolve()

    if requested is not None and not isinstance(requested, str):
        raise ValueError("Directory must be a string path")

    requested = requested.strip() if requested else requested
    if not requested:
        return str(base_path)

    def resolve_requested(path: str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = base_path / candidate
        return candidate.resolve()

    if not client.settings.a2a_allow_directory_override:
        requested_path = resolve_requested(requested)
        if requested_path == base_path:
            return str(base_path)
        raise ValueError("Directory override is disabled by service configuration")

    requested_path = resolve_requested(requested)
    try:
        requested_path.relative_to(base_path)
    except ValueError as err:
        raise ValueError(
            f"Directory {requested} is outside the allowed workspace {base_path}"
        ) from err

    return str(requested_path)
