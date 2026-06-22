"""Command-line parser and top-level dispatch.

Typical call stack:
  mwmap.py -> mwmap.cli.main() -> build_cli_parser() -> command run(args)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mwmap.commands.clone import run_clone
from mwmap.commands.fetch import run_fetch
from mwmap.commands.fsck import run_fsck
from mwmap.commands.init import run_init
from mwmap.commands.merge import run_merge
from mwmap.commands.pull import run_pull
from mwmap.commands.push import run_push
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
    p_clone.add_argument(
        "--follow",
        action="store_true",
        help="Follow a redirect to its target (default: clone the redirect page itself)",
    )
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

    p_fetch = sub.add_parser("fetch", help="Fetch upstream revisions into the cache")
    p_fetch.add_argument("path", nargs="?", help="Limit to one paired local path")
    p_fetch.set_defaults(func=run_fetch)

    p_merge = sub.add_parser("merge", help="Merge cached upstream into working files")
    p_merge.add_argument("path", nargs="?", help="Limit to one paired local path")
    p_merge.set_defaults(func=run_merge)

    p_pull = sub.add_parser("pull", help="Fetch then merge (git pull)")
    p_pull.add_argument("path", nargs="?", help="Limit to one paired local path")
    p_pull.set_defaults(func=run_pull)

    p_push = sub.add_parser("push", help="Upload local edits to MediaWiki")
    p_push.add_argument("path", nargs="?", help="Limit to one paired local path")
    p_push.add_argument("-m", "--summary", help="Edit summary (prompts an editor if omitted)")
    p_push.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be pushed without contacting MediaWiki",
    )
    p_push.set_defaults(func=run_push)

    p_fsck = sub.add_parser("fsck", help="Check cache and mapping integrity")
    p_fsck.set_defaults(func=run_fsck)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the selected subcommand."""
    parser = build_cli_parser()
    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    return args.func(args)
