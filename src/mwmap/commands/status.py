"""Implementation of `mwmap status`."""

from __future__ import annotations

import argparse

from mwmap.context import load_config


def run_status(args: argparse.Namespace) -> int:
    """Print configured remotes and the current mapping count."""
    config = load_config(args.root)
    remotes = config.get("remotes") or {}
    mappings = config.get("mappings") or []

    print(f"{len(remotes)} remotes")
    for name, remote in remotes.items():
        print(f"  {name} ({remote.get('type', 'unknown')}): {remote.get('location', '')}")
    print(f"{len(mappings)} mappings")
    return 0

