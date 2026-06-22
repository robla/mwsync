"""MediaWiki URL parsing and fetch helpers."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib import error, parse, request

from mwmap.core.misc import die


USER_AGENT = "mwmap/0.1 prototype"

# English redirect magic word; localized wikis use other words (best-effort).
_REDIRECT_RE = re.compile(
    r"^\s*#REDIRECT\s*\[\[\s*([^\]|#]+?)\s*(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]",
    re.IGNORECASE,
)


class MediaWikiError(Exception):
    """A transport/parse failure talking to the MediaWiki API.

    Raised by `_api_get` so callers choose fatality: the primary page fetch
    treats it as fatal (`die`), while auxiliary fetches (siteinfo) can warn.
    """


def parse_mediawiki_page_url(url: str) -> tuple[str, str, str]:
    """Return `(title, api_url, remote_location)` for a MediaWiki page URL."""
    parsed = parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        die(f"clone expects an absolute http(s) MediaWiki page URL: {url}")

    path = parsed.path
    query = parse.parse_qs(parsed.query)
    title = ""
    api_path = ""

    if "/wiki/" in path:
        prefix, raw_title = path.split("/wiki/", 1)
        title = parse.unquote(raw_title).replace("_", " ")
        api_path = f"{prefix}/w/api.php"
    elif path.endswith("/index.php") and query.get("title"):
        title = query["title"][0].replace("_", " ")
        api_path = f"{path.rsplit('/index.php', 1)[0]}/api.php"
    else:
        die("clone currently supports MediaWiki page URLs like https://host/wiki/Page")

    if not title:
        die(f"could not determine page title from URL: {url}")

    api_url = parse.urlunparse((parsed.scheme, parsed.netloc, api_path, "", "", ""))
    remote_location = parse.urlunparse(
        (parsed.scheme, parsed.netloc, api_path.rsplit("/api.php", 1)[0] + "/", "", "", "")
    )
    return title, api_url, remote_location


def _api_get(api_url: str, params: dict[str, str]) -> dict[str, Any]:
    """GET one MediaWiki API query and return the parsed JSON payload."""
    url = f"{api_url}?{parse.urlencode(params)}"
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        raise MediaWikiError(f"failed to reach MediaWiki API at {api_url}: {exc}")
    except json.JSONDecodeError as exc:
        raise MediaWikiError(f"MediaWiki returned invalid JSON from {api_url}: {exc}")


def fetch_mediawiki_page(
    api_url: str, title: str, follow_redirects: bool = False
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Fetch current wikitext and revision metadata for one page.

    By default the page named by `title` is returned verbatim, including a
    redirect's `#REDIRECT` stub, so the caller can warn and offer `--follow`.
    With `follow_redirects`, MediaWiki resolves the redirect to its target
    (like a browser). Returns `(content, metadata, redirect)`, where `redirect`
    is None or `{"followed": bool, "from": str, "to": str | None}`.
    """
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "revisions|info",
        "titles": title,
        "rvprop": "ids|timestamp|contentmodel|content",
        "rvslots": "main",
    }
    if follow_redirects:
        params["redirects"] = "1"

    try:
        payload = _api_get(api_url, params)
    except MediaWikiError as exc:
        die(str(exc))

    query = payload.get("query", {})
    pages = query.get("pages", [])
    if not pages:
        die(f"MediaWiki returned no page for title: {title}")
    page = pages[0]
    if page.get("missing"):
        die(f"MediaWiki page does not exist: {title}")

    revisions = page.get("revisions") or []
    if not revisions:
        die(f"MediaWiki returned no revisions for title: {title}")
    revision = revisions[0]
    slots = revision.get("slots") or {}
    main_slot = slots.get("main") or {}
    content = main_slot.get("content", revision.get("*"))
    if content is None:
        die(f"MediaWiki response did not include page content for: {title}")

    metadata = {
        "pageid": page.get("pageid"),
        "namespace": page.get("ns", 0),
        "namespace_name": _namespace_name_from_title(page.get("title", title), page.get("ns", 0)),
        "title": page.get("title", title),
        "revid": revision.get("revid"),
        "parentid": revision.get("parentid"),
        "timestamp": revision.get("timestamp"),
        "contentmodel": main_slot.get("contentmodel"),
        "redirect": bool(page.get("redirect")),
    }
    redirect = _redirect_note(title, page, query, content, follow_redirects)
    return content, metadata, redirect


def _namespace_name_from_title(title: str, namespace: int) -> str:
    """Return a display namespace name from API data, falling back carefully."""
    if namespace == 0:
        return "main"
    if ":" in title:
        return title.split(":", 1)[0]
    return str(namespace)


def _redirect_note(
    requested: str,
    page: dict[str, Any],
    query: dict[str, Any],
    content: str,
    follow_redirects: bool,
) -> dict[str, Any] | None:
    """Describe any redirect involved, for user messaging (not for caching)."""
    if follow_redirects:
        hops = query.get("redirects") or []
        if not hops:
            return None
        first = hops[0]
        return {
            "followed": True,
            "from": first.get("from", requested),
            "to": first.get("to", page.get("title")),
        }
    if page.get("redirect"):
        return {
            "followed": False,
            "from": page.get("title", requested),
            "to": _parse_redirect_target(content),
        }
    return None


def _parse_redirect_target(content: str) -> str | None:
    """Best-effort target of a `#REDIRECT [[...]]` stub (English magic word)."""
    if not content:
        return None
    match = _REDIRECT_RE.match(content)
    return match.group(1).strip() if match else None


def fetch_siteinfo(api_url: str) -> dict[str, Any]:
    """Fetch trimmed general + namespace siteinfo for a remote.

    The basis for future link rewriting and title<->URL mapping (wgServer,
    articlepath, the namespace table). Raises `MediaWikiError` on failure so
    the caller can keep this auxiliary fetch non-fatal.
    """
    payload = _api_get(
        api_url,
        {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "meta": "siteinfo",
            "siprop": "general|namespaces",
        },
    )
    query = payload.get("query", {})
    general = query.get("general", {})
    namespaces = {
        str(ns_id): {"name": ns.get("name", ""), "canonical": ns.get("canonical", "")}
        for ns_id, ns in (query.get("namespaces", {}) or {}).items()
    }
    return {
        "general": {
            "sitename": general.get("sitename"),
            "base": general.get("base"),
            "server": general.get("server"),
            "scriptpath": general.get("scriptpath"),
            "articlepath": general.get("articlepath"),
            "lang": general.get("lang"),
            "generator": general.get("generator"),
        },
        "namespaces": namespaces,
    }
