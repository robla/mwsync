"""Implementation of `mwmap clone`.

`clone` onboards one MediaWiki page URL end to end. It is deliberately
composed from the same primitives a future `fetch`/`restore` will use, rather
than reimplementing them, so it does not become the overloaded "does
everything" verb that legacy `checkout` was.

Typical call stack:
  run_clone()
    -> workspace.register_remote      # config: ensure remote exists
    -> core.remote.build_remote       # resolve remote backend
    -> sync.fetch_page                # remote -> cache (records revision)
    -> sync.write_local_body          # cache body -> working file
    -> workspace.upsert_page_mapping  # config: record pairing + base_revid
"""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib import parse

from mwmap.workspace import (
    init_workspace,
    load_workspace_config,
    register_remote,
    save_workspace_config,
    upsert_page_mapping,
)
from mwmap.core.mediawiki import parse_mediawiki_page_url
from mwmap.core.remote import build_remote
from mwmap.core.misc import die, local_path_for_title, remote_name_from_host
from mwmap.sync import fetch_page, write_local_body


def run_clone(args: argparse.Namespace) -> int:
    """Onboard one MediaWiki page URL into the local workspace."""
    init_workspace(args.root)
    config = load_workspace_config(args.root)

    title, _api_url, remote_location = parse_mediawiki_page_url(args.url)
    preferred_name = remote_name_from_host(parse.urlparse(args.url).hostname or "remote")
    remote_name = register_remote(config, preferred_name, "mediawiki", remote_location)
    remote = build_remote(remote_name, config["remotes"][remote_name])

    # Fetch first: this only populates the disposable cache, and we need the
    # normalized title and stable pageid before deciding the working path.
    content, metadata = fetch_page(args.root, remote, title)
    pageid = metadata.get("pageid")
    if pageid is None:
        die(f"MediaWiki did not return a page id for: {title}")
    canonical_title = metadata.get("title", title)

    fmt = "mw"
    local_path = Path(args.path) if args.path else local_path_for_title(canonical_title)
    target = args.root / local_path
    if target.exists():
        die(f"local path already exists: {target}")
    write_local_body(fmt, target, content)

    upsert_page_mapping(
        config,
        remote=remote_name,
        pageid=pageid,
        title=canonical_title,
        local_path=local_path.as_posix(),
        fmt=fmt,
        base_revid=metadata.get("revid"),
    )
    save_workspace_config(args.root, config)

    print(f"Cloned {canonical_title} (page {pageid}) to {local_path.as_posix()}")
    return 0
