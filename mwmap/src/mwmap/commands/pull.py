"""Implementation of `mwmap pull` — fetch then merge (mirrors `git pull`).

Typical call stack:
  run_pull() -> fetch.fetch_selected() -> merge.merge_selected()
"""

from __future__ import annotations

import argparse

from mwmap.commands.fetch import fetch_selected
from mwmap.commands.merge import merge_selected
from mwmap.workspace import iter_page_mappings, load_workspace_config


def run_pull(args: argparse.Namespace) -> int:
    """Fetch upstream for paired pages, then merge it into the working files."""
    config = load_workspace_config(args.root)
    mappings = iter_page_mappings(config, getattr(args, "path", None))
    if not mappings:
        print("no page mappings to pull")
        return 0
    fetch_selected(args.root, config, mappings)
    return merge_selected(args.root, config, mappings)
