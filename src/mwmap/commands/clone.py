"""Implementation of `mwmap clone`.

Typical call stack:
  run_clone()
    -> init/load config
    -> parse MediaWiki page URL
    -> fetch MediaWiki page
    -> write working file + revision cache
    -> record mapping
"""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib import parse

from mwmap.context import (
    cache_page,
    init_workspace,
    load_config,
    mapping_exists,
    save_config,
    unique_remote_name,
)
from mwmap.core.mediawiki import fetch_mediawiki_page, mediawiki_api_from_page_url
from mwmap.core.misc import atomic_write_text, die, local_path_for_title, remote_name_from_host


def run_clone(args: argparse.Namespace) -> int:
    """Onboard one MediaWiki page URL into the local workspace."""
    init_workspace(args.root)
    config = load_config(args.root)

    title, api_url, remote_location = mediawiki_api_from_page_url(args.url)
    preferred_name = remote_name_from_host(parse.urlparse(args.url).hostname or "remote")
    remote_name = unique_remote_name(config, preferred_name, remote_location)

    config.setdefault("remotes", {}).setdefault(
        remote_name,
        {"type": "mediawiki", "location": remote_location},
    )

    content, metadata = fetch_mediawiki_page(api_url, title)
    local_path = Path(args.path) if args.path else local_path_for_title(metadata["title"])
    target = args.root / local_path
    if target.exists():
        die(f"local path already exists: {target}")

    atomic_write_text(target, content)
    cache_page(args.root, remote_name, metadata["title"], content, metadata)

    local_path_text = local_path.as_posix()
    if not mapping_exists(config, remote_name, metadata["title"], local_path_text):
        config.setdefault("mappings", []).append(
            {
                "type": "page",
                "remote": remote_name,
                "remote_path": metadata["title"],
                "local_path": local_path_text,
            }
        )
    save_config(args.root, config)

    print(f"Cloned {metadata['title']} to {local_path_text}")
    return 0

