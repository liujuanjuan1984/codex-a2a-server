from __future__ import annotations

from typing import TYPE_CHECKING

from a2a.server.apps.jsonrpc.jsonrpc_app import DefaultCallContextBuilder
from fastapi import Request

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext


def _is_stream_request(request: Request) -> bool:
    path = request.url.path
    raw_path = request.scope.get("raw_path")
    raw_value = ""
    if isinstance(raw_path, (bytes, bytearray)):
        raw_value = raw_path.decode(errors="ignore")
    return (
        path.endswith("/v1/message:stream")
        or path.endswith("/v1/message%3Astream")
        or raw_value.endswith("/v1/message:stream")
        or raw_value.endswith("/v1/message%3Astream")
    )


class IdentityAwareCallContextBuilder(DefaultCallContextBuilder):
    def build(self, request: Request) -> ServerCallContext:
        context = super().build(request)
        if _is_stream_request(request):
            context.state["a2a_streaming_request"] = True

        identity = getattr(request.state, "user_identity", None)
        if identity:
            context.state["identity"] = identity
        correlation_id = getattr(request.state, "correlation_id", None)
        if isinstance(correlation_id, str) and correlation_id:
            context.state["correlation_id"] = correlation_id

        return context
