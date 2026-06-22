"""Content-sync operations shared by clone and (future) fetch/merge/push.

This is the separation the design calls for: pairing/config lives in
`workspace.py`, while moving content between a remote, the cache, and the
working tree lives here.

Typical call stack:
  commands.clone.run_clone()
    -> sync.fetch_page()        # remote -> cache (no working-tree change)
    -> sync.ensure_site_info()  # remote -> cache/<remote>/site.yaml (once)
    -> sync.write_local_body()  # cache/body -> working tree
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mwmap.core.mediawiki import MediaWikiError
from mwmap.core.misc import atomic_write_text, die
from mwmap.core.remote import Remote
from mwmap.workspace import cache_page_rev, save_site_info, site_info_path


def fetch_page(
    root: Path, remote: Remote, title: str, follow_redirects: bool = False
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Fetch one page from `remote` into the cache; return (body, meta, redirect).

    Mirrors `git fetch`: it touches only the disposable cache, never the
    working tree. `clone` composes this with `write_local_body`; a future
    `fetch` verb will call it directly.
    """
    content, metadata, redirect = remote.fetch_page(title, follow_redirects=follow_redirects)
    cache_page_rev(root, remote.name, content, metadata)
    return content, metadata, redirect


def ensure_site_info(root: Path, remote: Remote) -> None:
    """Fetch and cache `cache/<remote>/site.yaml` once per remote (non-fatal).

    Records server/articlepath/namespaces — the basis for future link
    rewriting and title<->URL mapping. A failure here only warns, since the
    page content it accompanies has already been fetched successfully.
    """
    path = site_info_path(root, remote.name)
    if path.exists():
        return
    try:
        info = remote.fetch_siteinfo()
    except MediaWikiError as exc:
        print(f"Warning: could not fetch siteinfo for {remote.name}: {exc}", file=sys.stderr)
        return
    save_site_info(root, remote.name, info)


SUPPORTED_FORMATS = {"mw"}


def write_local_body(fmt: str, path: Path, content: str) -> None:
    """Write a page body to the working tree in the mapping's local format.

    The single choke point where wikitext would convert to another local
    format (Org-mode / Markdown / Zim). Today only raw MediaWiki wikitext
    (`mw`) is supported; other formats plug in here.
    """
    if fmt not in SUPPORTED_FORMATS:
        die(f"unsupported local format: {fmt!r}")
    atomic_write_text(path, content)
