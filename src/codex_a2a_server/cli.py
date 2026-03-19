from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from importlib import resources
from pathlib import Path

from . import __version__


def _build_deploy_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    deploy_parser = subparsers.add_parser(
        "deploy",
        help="Deploy one release-based systemd instance.",
        description="Deploy one managed Codex + A2A systemd instance from the released CLI.",
    )
    deploy_parser.add_argument("--project", required=True, help="Project instance name.")
    deploy_parser.add_argument("--data-root", help="Per-project deployment root.")
    deploy_parser.add_argument("--a2a-port", type=int, help="A2A listen port.")
    deploy_parser.add_argument("--a2a-host", help="A2A bind host.")
    deploy_parser.add_argument("--a2a-public-url", help="Public A2A URL advertised by the server.")
    deploy_parser.add_argument(
        "--a2a-enable-health-endpoint",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable the authenticated /health endpoint and deploy readiness probe.",
    )
    deploy_parser.add_argument(
        "--a2a-enable-session-shell",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Expose codex.sessions.shell on the deployed instance.",
    )
    deploy_parser.add_argument(
        "--a2a-interrupt-request-ttl-seconds",
        type=int,
        help="TTL for pending interrupt callbacks.",
    )
    deploy_parser.add_argument("--a2a-log-level", help="A2A server log level.")
    deploy_parser.add_argument(
        "--a2a-log-payloads",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable request/response payload logging.",
    )
    deploy_parser.add_argument("--a2a-log-body-limit", type=int, help="Payload log body limit.")
    deploy_parser.add_argument("--codex-provider-id", help="Default Codex provider id.")
    deploy_parser.add_argument("--codex-model-id", help="Default Codex model id.")
    deploy_parser.add_argument(
        "--package-spec",
        help="Published package spec used when refreshing the shared runtime.",
    )
    deploy_parser.add_argument(
        "--codex-timeout", type=int, help="Codex request timeout in seconds."
    )
    deploy_parser.add_argument(
        "--codex-timeout-stream",
        type=int,
        help="Codex streaming timeout in seconds.",
    )
    deploy_parser.add_argument(
        "--git-identity-name",
        help="Git identity name configured in the workspace.",
    )
    deploy_parser.add_argument(
        "--git-identity-email",
        help="Git identity email configured in the workspace.",
    )
    deploy_parser.add_argument(
        "--enable-secret-persistence",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Persist A2A_BEARER_TOKEN and provider keys into root-only secret files.",
    )
    deploy_parser.add_argument(
        "--update-a2a",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Refresh the shared codex-a2a runtime from the published package.",
    )
    deploy_parser.add_argument(
        "--force-restart",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force a systemd restart even when the unit is already active.",
    )
    deploy_parser.epilog = (
        "Secrets such as A2A_BEARER_TOKEN and provider API keys remain "
        "environment-only inputs. Legacy key=value arguments are still accepted "
        "for compatibility, but flags are the preferred CLI contract."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-a2a-server",
        description="Codex A2A server CLI.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "serve",
        help="Start the A2A server using environment-based settings.",
    )
    _build_deploy_parser(subparsers)
    return parser


def _assets_scripts_dir() -> resources.abc.Traversable:
    return resources.files("codex_a2a_server.assets").joinpath("scripts")


def _serve_main() -> None:
    from .app import main as serve_main

    serve_main()


def _run_packaged_script(
    script_name: str,
    args: Sequence[str],
    *,
    env_overrides: Mapping[str, str] | None = None,
) -> int:
    with resources.as_file(_assets_scripts_dir()) as scripts_dir:
        script_path = Path(scripts_dir) / script_name
        if not script_path.is_file():
            print(f"Packaged release asset not found: {script_name}", file=sys.stderr)
            return 1
        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
        completed = subprocess.run(
            ["bash", str(script_path), *args],
            check=False,
            env=env,
        )
        return completed.returncode


def _is_legacy_passthrough(command: str, args: Sequence[str]) -> bool:
    if command != "deploy":
        return False
    return bool(args) and all("=" in arg and not arg.startswith("-") for arg in args)


def _append_key_value_arg(args: list[str], key: str, value: object | None) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    else:
        rendered = str(value)
    args.append(f"{key}={rendered}")


def _build_deploy_args(namespace: argparse.Namespace) -> list[str]:
    args: list[str] = []
    mappings: tuple[tuple[str, object | None], ...] = (
        ("project", namespace.project),
        ("data_root", namespace.data_root),
        ("a2a_port", namespace.a2a_port),
        ("a2a_host", namespace.a2a_host),
        ("a2a_public_url", namespace.a2a_public_url),
        ("a2a_enable_health_endpoint", namespace.a2a_enable_health_endpoint),
        ("a2a_enable_session_shell", namespace.a2a_enable_session_shell),
        (
            "a2a_interrupt_request_ttl_seconds",
            namespace.a2a_interrupt_request_ttl_seconds,
        ),
        ("a2a_log_level", namespace.a2a_log_level),
        ("a2a_log_payloads", namespace.a2a_log_payloads),
        ("a2a_log_body_limit", namespace.a2a_log_body_limit),
        ("codex_provider_id", namespace.codex_provider_id),
        ("codex_model_id", namespace.codex_model_id),
        ("package_spec", namespace.package_spec),
        ("codex_timeout", namespace.codex_timeout),
        ("codex_timeout_stream", namespace.codex_timeout_stream),
        ("git_identity_name", namespace.git_identity_name),
        ("git_identity_email", namespace.git_identity_email),
        ("enable_secret_persistence", namespace.enable_secret_persistence),
        ("update_a2a", namespace.update_a2a),
        ("force_restart", namespace.force_restart),
    )
    for key, value in mappings:
        _append_key_value_arg(args, key, value)
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if args and _is_legacy_passthrough(args[0], args[1:]):
        return _run_packaged_script("deploy.sh", args[1:])

    namespace = parser.parse_args(args)
    if namespace.command in {None, "serve"}:
        _serve_main()
        return 0
    if namespace.command == "deploy":
        return _run_packaged_script("deploy.sh", _build_deploy_args(namespace))

    parser.error(f"Unknown command: {namespace.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
