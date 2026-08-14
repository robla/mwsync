"""Implementation of `mwmap status`."""

from __future__ import annotations

import argparse

from mwmap.workspace import load_workspace_config


def run_status(args: argparse.Namespace) -> int:
    """Print configured remotes and the current mapping count."""
    config = load_workspace_config(args.root)
    remotes = config.get("remotes") or {}
    mappings = config.get("mappings") or []

    print(f"{len(remotes)} remotes")
    for name, remote in remotes.items():
        print(f"  {name} ({remote.get('type', 'unknown')}): {remote.get('location', '')}")
    print(f"{len(mappings)} mappings")
    for mapping in mappings:
        ident = f"{mapping.get('remote', '?')}:{mapping.get('remote_path', '?')}"
        pageid = mapping.get("pageid")
        if pageid is not None:
            ident += f" (page {pageid})"
        print(f"  {mapping.get('local_path', '?')} <- {ident}")
    return 0
