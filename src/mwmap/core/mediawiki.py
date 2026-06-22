"""MediaWiki URL parsing and fetch helpers."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request

from mwmap.core.misc import die


USER_AGENT = "mwmap/0.1 prototype"


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


def fetch_mediawiki_page(api_url: str, title: str) -> tuple[str, dict[str, Any]]:
    """Fetch current wikitext and revision metadata for one page."""
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "revisions",
        "titles": title,
        "rvprop": "ids|timestamp|content",
        "rvslots": "main",
    }
    url = f"{api_url}?{parse.urlencode(params)}"
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        die(f"failed to fetch page from MediaWiki: {exc}")
    except json.JSONDecodeError as exc:
        die(f"MediaWiki returned invalid JSON: {exc}")

    pages = payload.get("query", {}).get("pages", [])
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
        "title": page.get("title", title),
        "revid": revision.get("revid"),
        "parentid": revision.get("parentid"),
        "timestamp": revision.get("timestamp"),
    }
    return content, metadata
