"""Command-line parser and top-level dispatch.

Typical call stack:
  mwmap.py -> mwmap.cli.main() -> build_cli_parser() -> command run(args)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mwmap.commands.clone import run_clone
from mwmap.commands.init import run_init
from mwmap.commands.remote import run_remote_add
from mwmap.commands.status import run_status


def build_cli_parser() -> argparse.ArgumentParser:
    """Build the first-version mwmap command parser."""
    parser = argparse.ArgumentParser(prog="mwmap.py")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Workspace root containing _mwmap/ (default: current directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize an mwmap workspace")
    p_init.set_defaults(func=run_init)

    p_clone = sub.add_parser("clone", help="Onboard a MediaWiki page URL")
    p_clone.add_argument("url", help="MediaWiki page URL")
    p_clone.add_argument("path", nargs="?", help="Optional local output path")
    p_clone.set_defaults(func=run_clone)

    p_remote = sub.add_parser("remote", help="Manage remotes")
    remote_sub = p_remote.add_subparsers(dest="remote_command", required=True)
    p_remote_add = remote_sub.add_parser("add", help="Add a remote")
    p_remote_add.add_argument("name")
    p_remote_add.add_argument("type")
    p_remote_add.add_argument("location")
    p_remote_add.set_defaults(func=run_remote_add)

    p_status = sub.add_parser("status", help="Show workspace status")
    p_status.set_defaults(func=run_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the selected subcommand."""
    parser = build_cli_parser()
    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    return args.func(args)
