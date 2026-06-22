"""Implementation of `mwmap init`."""

from __future__ import annotations

import argparse

from mwmap.context import CONFIG_DIR, init_workspace


def run_init(args: argparse.Namespace) -> int:
    """Create `_mwmap/config.yaml` and `_mwmap/cache/` if needed."""
    created = init_workspace(args.root)
    if created:
        print(f"Initialized mwmap workspace in {args.root / CONFIG_DIR}")
    else:
        print(f"mwmap workspace already initialized in {args.root / CONFIG_DIR}")
    return 0

