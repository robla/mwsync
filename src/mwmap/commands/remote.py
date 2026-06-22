"""Implementation of `mwmap remote` subcommands."""

from __future__ import annotations

import argparse

from mwmap.context import load_config, save_config
from mwmap.core.misc import die


def run_remote_add(args: argparse.Namespace) -> int:
    """Record a named remote in `_mwmap/config.yaml`."""
    config = load_config(args.root)
    remotes = config.setdefault("remotes", {})
    if args.name in remotes:
        die(f"remote already exists: {args.name}")
    remotes[args.name] = {"type": args.type, "location": args.location}
    save_config(args.root, config)
    print(f"Added remote {args.name}")
    return 0

