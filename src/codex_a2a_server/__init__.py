"""A2A wrapper for codex."""

from importlib.metadata import PackageNotFoundError, version

_FALLBACK_VERSION = "0.1.0"

try:
    __version__ = version("codex-a2a-server")
except PackageNotFoundError:
    __version__ = _FALLBACK_VERSION
