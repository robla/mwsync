"""MediaWiki URL parsing and fetch helpers."""

from __future__ import annotations

import http.cookiejar
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


class MediaWikiEditConflict(MediaWikiError):
    """The page changed upstream since `baserevid`; the edit was refused.

    The caller should `fetch` + `merge` (i.e. `pull`) and retry the push.
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

    content, metadata = _extract_revision(page, title)
    redirect = _redirect_note(title, page, query, content, follow_redirects)
    return content, metadata, redirect


def fetch_mediawiki_page_by_id(api_url: str, pageid: Any) -> tuple[str, dict[str, Any]]:
    """Fetch current wikitext and metadata for an exact pageid.

    Used by `fetch`/`pull` for already-paired pages: identity is the stable
    pageid, so this still works after the page has been moved/renamed.
    """
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "revisions|info",
        "pageids": str(pageid),
        "rvprop": "ids|timestamp|contentmodel|content",
        "rvslots": "main",
    }
    try:
        payload = _api_get(api_url, params)
    except MediaWikiError as exc:
        die(str(exc))

    pages = payload.get("query", {}).get("pages", [])
    if not pages:
        die(f"MediaWiki returned no page for pageid: {pageid}")
    page = pages[0]
    if page.get("missing"):
        die(f"MediaWiki page id no longer exists: {pageid}")
    return _extract_revision(page, str(pageid))


def _extract_revision(page: dict[str, Any], fallback_title: str) -> tuple[str, dict[str, Any]]:
    """Pull the current revision body and metadata out of an API page object."""
    revisions = page.get("revisions") or []
    if not revisions:
        die(f"MediaWiki returned no revisions for: {fallback_title}")
    revision = revisions[0]
    slots = revision.get("slots") or {}
    main_slot = slots.get("main") or {}
    content = main_slot.get("content", revision.get("*"))
    if content is None:
        die(f"MediaWiki response did not include page content for: {fallback_title}")

    title = page.get("title", fallback_title)
    namespace = page.get("ns", 0)
    metadata = {
        "pageid": page.get("pageid"),
        "namespace": namespace,
        "namespace_name": _namespace_name_from_title(title, namespace),
        "title": title,
        "revid": revision.get("revid"),
        "parentid": revision.get("parentid"),
        "timestamp": revision.get("timestamp"),
        "contentmodel": main_slot.get("contentmodel"),
        "redirect": bool(page.get("redirect")),
    }
    return content, metadata


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


# ---------------------------------------------------------------------------
# Write side (login / CSRF / edit), copied and adapted from legacy mwsync.py
# (_mw_login / _mw_get_csrf_token / _mw_edit_page; see docs/legacy-code-copy.md).
# Adaptations: api_base -> api_url; edit by stable pageid; transport failures
# raise MediaWikiError and edit conflicts raise MediaWikiEditConflict.
# ---------------------------------------------------------------------------

def mediawiki_login(
    api_url: str, username: str, password: str
) -> request.OpenerDirector:
    """Log in with a MediaWiki bot password; return an authenticated opener."""
    jar = http.cookiejar.CookieJar()
    opener = request.build_opener(request.HTTPCookieProcessor(jar))

    # Step 1: get a login token.
    params = parse.urlencode(
        {"action": "query", "meta": "tokens", "type": "login", "format": "json"}
    )
    req = request.Request(f"{api_url}?{params}", headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.URLError as exc:
        raise MediaWikiError(f"failed to reach MediaWiki API at {api_url}: {exc}")
    login_token = data.get("query", {}).get("tokens", {}).get("logintoken")
    if not login_token:
        raise MediaWikiError("failed to get a login token from the MediaWiki API")

    # Step 2: POST the login.
    login_data = parse.urlencode(
        {
            "action": "login",
            "lgname": username,
            "lgpassword": password,
            "lgtoken": login_token,
            "format": "json",
        }
    ).encode("utf-8")
    req = request.Request(api_url, data=login_data, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except error.URLError as exc:
        raise MediaWikiError(f"failed to reach MediaWiki API at {api_url}: {exc}")

    login_result = result.get("login", {}).get("result")
    if login_result != "Success":
        reason = result.get("login", {}).get("reason", login_result or "unknown error")
        raise MediaWikiError(f"MediaWiki login failed: {reason}")
    return opener


def mediawiki_csrf_token(api_url: str, opener: request.OpenerDirector) -> str:
    """Get a CSRF edit token using an authenticated opener."""
    params = parse.urlencode(
        {"action": "query", "meta": "tokens", "type": "csrf", "format": "json"}
    )
    req = request.Request(f"{api_url}?{params}", headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.URLError as exc:
        raise MediaWikiError(f"failed to reach MediaWiki API at {api_url}: {exc}")
    token = data.get("query", {}).get("tokens", {}).get("csrftoken")
    if not token:
        raise MediaWikiError("failed to get a CSRF token from the MediaWiki API")
    return token


def mediawiki_edit_page(
    api_url: str,
    opener: request.OpenerDirector,
    *,
    pageid: Any,
    text: str,
    baserevid: Any,
    csrf_token: str,
    summary: str,
) -> int:
    """Submit an edit to an existing page (by pageid). Return the new revid.

    Passes `baserevid` so MediaWiki rejects the edit with `editconflict` if the
    page changed upstream since the local base — surfaced as MediaWikiEditConflict.
    """
    params = {
        "action": "edit",
        "pageid": str(pageid),
        "text": text,
        "token": csrf_token,
        "summary": summary,
        "baserevid": str(baserevid),
        "format": "json",
    }
    edit_data = parse.urlencode(params).encode("utf-8")
    req = request.Request(api_url, data=edit_data, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.URLError as exc:
        raise MediaWikiError(f"failed to reach MediaWiki API at {api_url}: {exc}")

    if "error" in data:
        code = data["error"].get("code", "unknown")
        info = data["error"].get("info", "unknown error")
        if code == "editconflict":
            raise MediaWikiEditConflict(
                f"edit conflict: page changed upstream since revid {baserevid}"
            )
        raise MediaWikiError(f"MediaWiki edit failed ({code}): {info}")

    edit_result = data.get("edit", {})
    if edit_result.get("result") != "Success":
        raise MediaWikiError(f"unexpected edit result: {edit_result.get('result', 'unknown')}")
    return edit_result.get("newrevid", 0)
