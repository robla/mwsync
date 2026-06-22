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
from mwmap.core.remote import Remote, build_remote
from mwmap.workspace import (
    cache_page_rev,
    save_site_info,
    site_info_path,
    update_cache_base,
)


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


def fetch_mapping(root: Path, remote: Remote, mapping: dict[str, Any]) -> dict[str, Any]:
    """Fetch the latest revision for a paired page (by stable pageid) into cache.

    Returns the fetched metadata. Like `git fetch`, it updates only the cache
    (`current_revid`, bodies, history) — never the working tree or `base_revid`.
    """
    content, metadata = remote.fetch_page_by_id(mapping["pageid"])
    cache_page_rev(root, remote.name, content, metadata)
    return metadata


def push_mapping(
    root: Path,
    remote: Remote,
    mapping: dict[str, Any],
    text: str,
    summary: str,
    baserevid: Any,
) -> int:
    """Push staged `text` for one mapping, re-cache the result, advance the base.

    Submits the edit with `baserevid` (the revision the staged content was based
    on) as the edit-conflict guard, then re-fetches the page (now the just-created
    revision) into cache and advances the cached base to it. Returns the new
    revid. The caller advances `base_revid` in `config.yaml` (the durable source
    of truth) and clears the pending commit.
    """
    new_revid = remote.push_page(mapping["pageid"], text, baserevid, summary)
    fetch_mapping(root, remote, mapping)
    update_cache_base(root, remote.name, mapping["pageid"], new_revid)
    return new_revid


def remote_for_mapping(
    config: dict[str, Any], mapping: dict[str, Any], cache: dict[str, Remote]
) -> Remote:
    """Resolve (and memoize) the Remote backend a mapping tracks."""
    name = mapping.get("remote")
    if name not in cache:
        definition = (config.get("remotes") or {}).get(name)
        if not definition:
            die(f"mapping references unknown remote: {name!r}")
        cache[name] = build_remote(name, definition)
    return cache[name]


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
