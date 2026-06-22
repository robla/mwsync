"""Remote abstraction: resolve a configured remote to an object that can sync.

A *remote* is any store mwmap syncs against (see config `remotes:`). This module
is the single seam where a new remote backend plugs in; commands and the sync
layer talk to the `Remote` protocol, never to a concrete backend directly.
"""

from __future__ import annotations

from typing import Any, Protocol

from mwmap.core.mediawiki import (
    fetch_mediawiki_page,
    fetch_mediawiki_page_by_id,
    fetch_siteinfo,
    mediawiki_csrf_token,
    mediawiki_edit_page,
    mediawiki_login,
)
from mwmap.core.misc import die


class Remote(Protocol):
    """A configured store mwmap fetches from and (eventually) pushes to."""

    name: str

    def fetch_page(
        self, title: str, follow_redirects: bool = False
    ) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
        """Return (body, revision-metadata, redirect-note) for one page."""
        ...

    def fetch_page_by_id(self, pageid: Any) -> tuple[str, dict[str, Any]]:
        """Return (body, revision-metadata) for an exact pageid."""
        ...

    def fetch_siteinfo(self) -> dict[str, Any]:
        """Return remote-wide metadata (server, paths, namespaces)."""
        ...

    def login(self, username: str, password: str) -> None:
        """Authenticate for subsequent `push_page` calls (write side)."""
        ...

    def push_page(self, pageid: Any, text: str, baserevid: Any, summary: str) -> int:
        """Submit an edit to one page; return the new revid. Requires login."""
        ...


class MediaWikiRemote:
    """A MediaWiki instance addressed by its API base location."""

    type = "mediawiki"

    def __init__(self, name: str, location: str) -> None:
        self.name = name
        self.location = location
        self._opener = None
        self._csrf_token: str | None = None

    @property
    def api_url(self) -> str:
        """Return the api.php endpoint derived from this remote's location."""
        return f"{self.location.rstrip('/')}/api.php"

    def fetch_page(
        self, title: str, follow_redirects: bool = False
    ) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
        """Fetch one page's current wikitext, metadata, and redirect note."""
        return fetch_mediawiki_page(self.api_url, title, follow_redirects=follow_redirects)

    def fetch_page_by_id(self, pageid: Any) -> tuple[str, dict[str, Any]]:
        """Fetch one page's current wikitext and metadata by exact pageid."""
        return fetch_mediawiki_page_by_id(self.api_url, pageid)

    def fetch_siteinfo(self) -> dict[str, Any]:
        """Fetch trimmed general + namespace siteinfo for this remote."""
        return fetch_siteinfo(self.api_url)

    def login(self, username: str, password: str) -> None:
        """Authenticate once; cache the opener + CSRF token for this session."""
        self._opener = mediawiki_login(self.api_url, username, password)
        self._csrf_token = mediawiki_csrf_token(self.api_url, self._opener)

    def push_page(self, pageid: Any, text: str, baserevid: Any, summary: str) -> int:
        """Submit an edit to one page by pageid; return the new revid."""
        if self._opener is None or self._csrf_token is None:
            raise RuntimeError("push_page requires login() first")
        return mediawiki_edit_page(
            self.api_url,
            self._opener,
            pageid=pageid,
            text=text,
            baserevid=baserevid,
            csrf_token=self._csrf_token,
            summary=summary,
        )


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
