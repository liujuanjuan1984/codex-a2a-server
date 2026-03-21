from __future__ import annotations

from typing import Any

from codex_a2a_server.config import Settings


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "a2a_bearer_token": "test-token",
    }
    base.update(overrides)
    return Settings(**base)
