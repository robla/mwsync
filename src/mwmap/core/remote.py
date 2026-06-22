"""Remote abstraction: resolve a configured remote to an object that can sync.

A *remote* is any store mwmap syncs against (see config `remotes:`). This module
is the single seam where a new remote backend plugs in; commands and the sync
layer talk to the `Remote` protocol, never to a concrete backend directly.
"""

from __future__ import annotations

from typing import Any, Protocol

from mwmap.core.mediawiki import fetch_mediawiki_page, fetch_siteinfo
from mwmap.core.misc import die


class Remote(Protocol):
    """A configured store mwmap fetches from and (eventually) pushes to."""

    name: str

    def fetch_page(
        self, title: str, follow_redirects: bool = False
    ) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
        """Return (body, revision-metadata, redirect-note) for one page."""
        ...

    def fetch_siteinfo(self) -> dict[str, Any]:
        """Return remote-wide metadata (server, paths, namespaces)."""
        ...


class MediaWikiRemote:
    """A MediaWiki instance addressed by its API base location."""

    type = "mediawiki"

    def __init__(self, name: str, location: str) -> None:
        self.name = name
        self.location = location

    @property
    def api_url(self) -> str:
        """Return the api.php endpoint derived from this remote's location."""
        return f"{self.location.rstrip('/')}/api.php"

    def fetch_page(
        self, title: str, follow_redirects: bool = False
    ) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
        """Fetch one page's current wikitext, metadata, and redirect note."""
        return fetch_mediawiki_page(self.api_url, title, follow_redirects=follow_redirects)

    def fetch_siteinfo(self) -> dict[str, Any]:
        """Fetch trimmed general + namespace siteinfo for this remote."""
        return fetch_siteinfo(self.api_url)


_REMOTE_TYPES = {MediaWikiRemote.type: MediaWikiRemote}


def build_remote(name: str, definition: dict[str, Any]) -> Remote:
    """Construct a Remote object from a config `remotes[name]` entry."""
    remote_type = definition.get("type")
    location = definition.get("location")
    cls = _REMOTE_TYPES.get(remote_type)
    if cls is None:
        die(f"unsupported remote type: {remote_type!r} (remote {name!r})")
    if not location:
        die(f"remote {name!r} has no location")
    return cls(name, location)
