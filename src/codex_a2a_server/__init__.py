"""A2A wrapper for codex."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

_PACKAGE_NAME = "codex-a2a-server"
_UNKNOWN_VERSION = "0.0.0+unknown"


def _package_version() -> str | None:
    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return None


def _scm_version() -> str | None:
    try:
        from setuptools_scm import get_version
    except ImportError:
        return None

    try:
        return get_version(root="../..", relative_to=__file__)
    except LookupError:
        return None


def _resolve_version() -> str:
    return _package_version() or _scm_version() or _UNKNOWN_VERSION


__version__ = _resolve_version()
