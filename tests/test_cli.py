from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import codex_a2a_server.cli as cli
from codex_a2a_server import __version__


def test_cli_help_does_not_require_runtime_settings(capsys: pytest.CaptureFixture[str]) -> None:
    with mock.patch("codex_a2a_server.cli._serve_main") as serve_mock:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--help"])

    assert excinfo.value.code == 0
    assert "Codex A2A server CLI." in capsys.readouterr().out
    serve_mock.assert_not_called()


def test_cli_version_does_not_require_runtime_settings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with mock.patch("codex_a2a_server.cli._serve_main") as serve_mock:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--version"])

    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out
    serve_mock.assert_not_called()


def test_cli_defaults_to_serve_when_no_subcommand() -> None:
    with mock.patch("codex_a2a_server.cli._serve_main") as serve_mock:
        assert cli.main([]) == 0

    serve_mock.assert_called_once_with()
