from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__


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
    return parser


def _serve_main() -> None:
    from .server.application import main as serve_main

    serve_main()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    _serve_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
