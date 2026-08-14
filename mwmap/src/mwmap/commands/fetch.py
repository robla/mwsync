"""Implementation of `mwmap fetch`.

Downloads the latest upstream revision for each paired page into the cache,
without touching the working tree (mirrors `git fetch`). `merge` then
integrates what `fetch` brought down; `pull` does both.

Typical call stack:
  run_fetch() -> fetch_selected() -> sync.fetch_mapping() per mapping
"""

from __future__ import annotations

import argparse

from mwmap.sync import fetch_mapping, remote_for_mapping
from mwmap.workspace import iter_page_mappings, load_workspace_config
from mwmap.core.remote import Remote


def run_fetch(args: argparse.Namespace) -> int:
    """Fetch upstream revisions for all paired pages (or one path) into cache."""
    config = load_workspace_config(args.root)
    mappings = iter_page_mappings(config, getattr(args, "path", None))
    if not mappings:
        print("no page mappings to fetch")
        return 0
    fetch_selected(args.root, config, mappings)
    return 0


def fetch_selected(root, config, mappings) -> None:
    """Fetch the given mappings into cache and report each result."""
    remotes: dict[str, Remote] = {}
    for mapping in mappings:
        remote = remote_for_mapping(config, mapping, remotes)
        metadata = fetch_mapping(root, remote, mapping)
        new_revid = metadata.get("revid")
        base_revid = mapping.get("base_revid")
        label = f"{mapping.get('remote')}:{mapping.get('remote_path')}"
        if new_revid == base_revid:
            print(f"{label}: up to date (rev {new_revid})")
        else:
            print(f"{label}: fetched rev {new_revid} (base {base_revid})")
