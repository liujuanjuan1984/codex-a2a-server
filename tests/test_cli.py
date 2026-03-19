from __future__ import annotations

import sys
from importlib import resources
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
    assert "serve" in capsys.readouterr().out
    serve_mock.assert_not_called()


def test_cli_deploy_help_exposes_flag_contract(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["deploy", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "--project" in help_text
    assert "--a2a-port" in help_text
    assert "--a2a-enable-health-endpoint" in help_text
    assert "Secrets such as A2A_BEARER_TOKEN" in help_text
    assert "Legacy key=value arguments are still accepted" in help_text
    assert "--repo-url" not in help_text


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


def test_cli_serve_subcommand_invokes_runtime() -> None:
    with mock.patch("codex_a2a_server.cli._serve_main") as serve_mock:
        assert cli.main(["serve"]) == 0

    serve_mock.assert_called_once_with()


def test_cli_deploy_subcommand_supports_legacy_key_value_args() -> None:
    with mock.patch("codex_a2a_server.cli._run_packaged_script", return_value=0) as run_mock:
        assert cli.main(["deploy", "project=alpha", "a2a_port=8010"]) == 0

    run_mock.assert_called_once_with(
        "deploy.sh",
        ["project=alpha", "a2a_port=8010"],
    )


def test_cli_deploy_subcommand_maps_flags_to_key_value_args() -> None:
    with mock.patch("codex_a2a_server.cli._run_packaged_script", return_value=0) as run_mock:
        assert (
            cli.main(
                [
                    "deploy",
                    "--project",
                    "alpha",
                    "--a2a-port",
                    "8010",
                    "--a2a-host",
                    "127.0.0.1",
                    "--a2a-enable-health-endpoint",
                    "--no-a2a-enable-session-shell",
                    "--enable-secret-persistence",
                    "--update-a2a",
                    "--force-restart",
                ]
            )
            == 0
        )

    run_mock.assert_called_once_with(
        "deploy.sh",
        [
            "project=alpha",
            "a2a_port=8010",
            "a2a_host=127.0.0.1",
            "a2a_enable_health_endpoint=true",
            "a2a_enable_session_shell=false",
            "enable_secret_persistence=true",
            "update_a2a=true",
            "force_restart=true",
        ],
    )


def test_cli_packages_deploy_scripts_as_assets() -> None:
    assets_root = resources.files("codex_a2a_server.assets").joinpath("scripts")
    assert assets_root.joinpath("deploy.sh").is_file()
    assert assets_root.joinpath("uninstall.sh").is_file()
    assert assets_root.joinpath("deploy", "enable_instance.sh").is_file()
