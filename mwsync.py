#!/usr/bin/env python3
"""
mwsync.py — Per-article local ↔ MediaWiki sync tool.

Subcommands:
  init      Create a minimal mwsync.yaml
  add       Register a new article by URL
  checkout  Register, fetch, and merge an article into a local .mw file
  fetch     Pull current wikitext and metadata into _cache
  push      Submit local edits back to the wiki
  diff      Compare upstream cache vs working local file
  difftool  Launch meld to compare upstream cache vs working local
  merge     Merge fetched upstream changes into local file
  log       Show cached revision history
  show      Print cached revision text
  fsck      Check cache refs, history, and revision files
  status    Show sync state of tracked articles

Usage:
  mwsync.py init
  mwsync.py add https://electowiki.org/wiki/Maine
  mwsync.py checkout https://electowiki.org/wiki/Maine
  mwsync.py checkout Maine@upstream^ --to scratch/Maine-old.mw
  mwsync.py fetch Maine
  mwsync.py diff Maine
  mwsync.py diff Maine@upstream^ Maine@upstream
  mwsync.py merge Maine
  mwsync.py push Maine -m "Update Maine article"
  mwsync.py status

Credentials (for push):
  MWSYNC_MW_USER      MediaWiki bot username
  MWSYNC_MW_PASSWORD  MediaWiki bot password
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.cookiejar
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

try:
    import yaml
except ImportError:
    yaml = None

DEFAULT_CONFIG_PATH = "mwsync.yaml"
DEFAULT_API_BASE = "https://electowiki.org/w/api.php"
DEFAULT_HISTORY_DEPTH = 50
USER_AGENT = "mwsync/1.0 (+https://electowiki.org/)"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    if yaml is None:
        print("Error: pyyaml is not installed. Run: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(path):
        print(f"Error: config file not found: {path}", file=sys.stderr)
        print("Create a mwsync.yaml or run mwsync.py from a directory that has one.", file=sys.stderr)
        sys.exit(1)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        sys.exit(1)


def save_config(config: dict, path: str = DEFAULT_CONFIG_PATH) -> bool:
    try:
        dir_path = os.path.dirname(os.path.abspath(path))
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".yaml.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
            os.replace(tmp_path, path)
            return True
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(f"Error writing {path}: {e}", file=sys.stderr)
        return False


def minimal_config() -> dict:
    return {
        "wiki": {
            "api_base": DEFAULT_API_BASE,
            "articles": {},
        },
    }


def _parse_article_url(url: str) -> tuple[str, str, str]:
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        print(f"Error: invalid URL: {url}", file=sys.stderr)
        sys.exit(1)
    if "/wiki/" not in parsed.path:
        print(f"Error: URL does not look like a /wiki/ page: {url}", file=sys.stderr)
        sys.exit(1)

    title_encoded = parsed.path.split("/wiki/", 1)[1]
    title = urllib.parse.unquote(title_encoded).replace("_", " ")
    key = urllib.parse.unquote(title_encoded).replace(" ", "_")
    local = key + ".mw"
    return key, title, local


def _looks_like_article_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return bool(parsed.scheme and parsed.netloc and "/wiki/" in parsed.path)


def _register_article_url(config: dict, config_path: str, url: str,
                          allow_existing: bool = False) -> tuple[str, dict, bool]:
    key, title, local = _parse_article_url(url)
    wiki = config.setdefault("wiki", {})
    articles = wiki.setdefault("articles", {})

    if key in articles:
        if allow_existing:
            return key, articles[key], False
        print(f"Error: article '{key}' is already registered in {config_path}.", file=sys.stderr)
        sys.exit(1)

    articles[key] = {
        "title": title,
        "url": url,
        "local": local,
    }
    if not save_config(config, config_path):
        sys.exit(1)
    return key, articles[key], True


def resolve_article_entry(config: dict, key: str) -> tuple[str, dict]:
    """Look up article entry by key or local filename; return canonical key and entry."""
    articles = config.get("wiki", {}).get("articles", {})
    if key in articles:
        return key, articles[key]

    local_matches = [
        (article_key, art)
        for article_key, art in articles.items()
        if art.get("local", article_key + ".mw") == key
    ]
    if len(local_matches) == 1:
        return local_matches[0]
    if len(local_matches) > 1:
        print(
            f"Error: local filename '{key}' matches multiple articles in mwsync.yaml.",
            file=sys.stderr,
        )
        sys.exit(1)

    known = list(articles.keys())
    print(f"Error: article '{key}' not found in mwsync.yaml.", file=sys.stderr)
    if known:
        print(f"Known articles: {', '.join(known)}", file=sys.stderr)
    else:
        print("No articles registered yet. Use 'mwsync.py add URL' to add one.",
              file=sys.stderr)
    sys.exit(1)


def resolve_article(config: dict, key: str) -> dict:
    """Compatibility wrapper returning only the article entry."""
    return resolve_article_entry(config, key)[1]


def get_api_base(config: dict) -> str:
    return config.get("wiki", {}).get("api_base", DEFAULT_API_BASE)


# ---------------------------------------------------------------------------
# MediaWiki API
# ---------------------------------------------------------------------------

def _fetch_page(title: str, api_base: str = DEFAULT_API_BASE) -> dict:
    """Fetch page wikitext and revision metadata from MediaWiki API.

    Returns dict with keys: wikitext, revid, parentid, timestamp, user,
    comment, sha1, size, contentmodel, contentformat
    """
    params = {
        "action": "query",
        "format": "json",
        "prop": "revisions",
        "rvprop": "content|ids|timestamp|user|comment|sha1|size",
        "titles": title,
        "formatversion": "2",
    }
    url = api_base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    pages = data.get("query", {}).get("pages", [])
    if not pages:
        raise ValueError(f"No pages found for title '{title}'")

    page = pages[0]
    if page.get("missing"):
        raise ValueError(f"Page '{title}' does not exist on wiki")

    revs = page.get("revisions", [])
    if not revs:
        raise ValueError(f"No revisions found for '{title}'")

    rev = revs[0]
    return {
        "wikitext": rev.get("content", ""),
        "revid": rev.get("revid", 0),
        "parentid": rev.get("parentid", 0),
        "timestamp": rev.get("timestamp", ""),
        "user": rev.get("user", ""),
        "comment": rev.get("comment", ""),
        "sha1": rev.get("sha1", ""),
        "size": rev.get("size", 0),
        "contentmodel": rev.get("contentmodel", ""),
        "contentformat": rev.get("contentformat", ""),
    }


def _fetch_revision_metadata(title: str, api_base: str, limit: int | None) -> list[dict]:
    """Fetch newest revision metadata without revision bodies."""
    if limit is not None and limit <= 0:
        return []

    revisions = []
    continuation = {}
    while limit is None or len(revisions) < limit:
        remaining = 500 if limit is None else limit - len(revisions)
        batch_limit = min(remaining, 500)
        params = {
            "action": "query",
            "format": "json",
            "prop": "revisions",
            "rvprop": "ids|timestamp|user|comment|sha1|size",
            "rvlimit": str(batch_limit),
            "titles": title,
            "formatversion": "2",
        }
        params.update(continuation)
        url = api_base + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        pages = data.get("query", {}).get("pages", [])
        if not pages:
            raise ValueError(f"No pages found for title '{title}'")
        page = pages[0]
        if page.get("missing"):
            raise ValueError(f"Page '{title}' does not exist on wiki")
        revisions.extend(page.get("revisions", []))

        continuation = data.get("continue", {})
        if not continuation:
            break

    return revisions if limit is None else revisions[:limit]


def _fetch_revision_by_revid(revid: int, api_base: str) -> dict:
    """Fetch one revision body and metadata by MediaWiki revid."""
    params = {
        "action": "query",
        "format": "json",
        "prop": "revisions",
        "rvprop": "content|ids|timestamp|user|comment|sha1|size",
        "revids": str(int(revid)),
        "formatversion": "2",
    }
    url = api_base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    pages = data.get("query", {}).get("pages", [])
    if not pages:
        raise ValueError(f"No page found for revid {revid}")
    revs = pages[0].get("revisions", [])
    if not revs:
        raise ValueError(f"No revision found for revid {revid}")
    rev = revs[0]
    return {
        "wikitext": rev.get("content", ""),
        "revid": rev.get("revid", revid),
        "parentid": rev.get("parentid", 0),
        "timestamp": rev.get("timestamp", ""),
        "user": rev.get("user", ""),
        "comment": rev.get("comment", ""),
        "sha1": rev.get("sha1", ""),
        "size": rev.get("size", 0),
        "contentmodel": rev.get("contentmodel", ""),
        "contentformat": rev.get("contentformat", ""),
    }


def _atomic_write(path: str, content: str) -> bool:
    """Atomically write text content to path. Returns True on success."""
    dir_path = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_path, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".mw.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
            return True
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(f"Error writing {path}: {e}", file=sys.stderr)
        return False


def _mw_login(api_base: str, username: str, password: str) -> urllib.request.OpenerDirector:
    """Log in to MediaWiki using bot password; return authenticated opener."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # Step 1: get login token
    params = urllib.parse.urlencode({
        "action": "query", "meta": "tokens", "type": "login", "format": "json",
    })
    req = urllib.request.Request(f"{api_base}?{params}", headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    login_token = data.get("query", {}).get("tokens", {}).get("logintoken")
    if not login_token:
        raise ValueError("Failed to get login token from MediaWiki API")

    # Step 2: POST login
    login_data = urllib.parse.urlencode({
        "action": "login",
        "lgname": username,
        "lgpassword": password,
        "lgtoken": login_token,
        "format": "json",
    }).encode("utf-8")
    req = urllib.request.Request(api_base, data=login_data,
                                 headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    login_result = result.get("login", {}).get("result")
    if login_result != "Success":
        reason = result.get("login", {}).get("reason", login_result or "unknown error")
        raise ValueError(f"MediaWiki login failed: {reason}")

    return opener


def _mw_get_csrf_token(api_base: str, opener: urllib.request.OpenerDirector) -> str:
    """Get CSRF edit token using authenticated opener."""
    params = urllib.parse.urlencode({
        "action": "query", "meta": "tokens", "type": "csrf", "format": "json",
    })
    req = urllib.request.Request(f"{api_base}?{params}", headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    token = data.get("query", {}).get("tokens", {}).get("csrftoken")
    if not token:
        raise ValueError("Failed to get CSRF token from MediaWiki API")
    return token


def _mw_edit_page(api_base: str, opener: urllib.request.OpenerDirector,
                  title: str, text: str, baserevid: int,
                  csrf_token: str, summary: str,
                  create_new: bool = False) -> int:
    """Submit a page edit to MediaWiki. Returns new revid on success."""
    params = {
        "action": "edit",
        "title": title,
        "text": text,
        "token": csrf_token,
        "summary": summary,
        "format": "json",
    }
    if create_new:
        params["createonly"] = "1"
    else:
        params["baserevid"] = str(baserevid)
    edit_data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(api_base, data=edit_data,
                                 headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if "error" in data:
        code = data["error"].get("code", "unknown")
        info = data["error"].get("info", "unknown error")
        if code == "editconflict":
            raise ValueError(
                f"Edit conflict: page was edited since revid {baserevid}. "
                f"Run 'mwsync.py fetch ARTICLE' to get the latest version, then retry."
            )
        raise ValueError(f"MediaWiki edit failed ({code}): {info}")

    edit_result = data.get("edit", {})
    if edit_result.get("result") != "Success":
        raise ValueError(f"Unexpected edit result: {edit_result.get('result', 'unknown')}")

    return edit_result.get("newrevid", 0)


def _edit_summary(default: str, key: str, title: str, baserevid: int) -> str | None:
    """Open $VISUAL/$EDITOR for edit summary. Returns summary string or None to abort."""
    comment_block = (
        f"\n# Edit summary for push to wiki.\n"
        f"# Lines starting with '#' are stripped.\n"
        f"# An empty summary aborts the push.\n"
        f"#\n"
        f"# Article: {key}\n"
        f"# Page:    {title}\n"
        f"# Base revid: {baserevid}\n"
    )
    fd, tmp_path = tempfile.mkstemp(suffix=".mwsync-summary.txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(default + comment_block)
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        subprocess.run([editor, tmp_path])
        with open(tmp_path, "r", encoding="utf-8") as f:
            raw = f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    lines = [line for line in raw.splitlines() if not line.startswith("#")]
    summary = "\n".join(lines).strip()
    return summary if summary else None


def _server_snapshot_path(key: str) -> str:
    return os.path.join("_cache", f"server--{key}.mw")


def _cache_dir(key: str) -> str:
    return os.path.join("_cache", key)


def _history_path(key: str) -> str:
    return os.path.join(_cache_dir(key), "history.jsonl")


def _revision_body_path(key: str, revid: int | str) -> str:
    return os.path.join(_cache_dir(key), f"{revid}.mw")


def _revision_meta_path(key: str, revid: int | str) -> str:
    return os.path.join(_cache_dir(key), f"{revid}.json")


def _ref_path(key: str, ref: str) -> str:
    return os.path.join(_cache_dir(key), "refs", ref)


def _legacy_cache_exists(key: str) -> bool:
    return os.path.exists(_server_snapshot_path(key)) and not os.path.exists(_history_path(key))


def _check_legacy_cache(key: str) -> None:
    legacy = _server_snapshot_path(key)
    if not _legacy_cache_exists(key):
        return
    print(f"Error: legacy cache detected: {legacy}", file=sys.stderr)
    print(
        f"This version expects {_history_path(key)} and revid-named files.",
        file=sys.stderr,
    )
    print("Remove the legacy snapshot and fetch again, or run a migration tool.",
          file=sys.stderr)
    sys.exit(1)


def _write_json(path: str, data: dict) -> bool:
    content = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return _atomic_write(path, content)


def _read_ref(key: str, ref: str) -> int | None:
    path = _ref_path(key, ref)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print(f"Error: invalid ref value in {path}: {raw}", file=sys.stderr)
        sys.exit(1)


def _write_ref(key: str, ref: str, revid: int) -> bool:
    return _atomic_write(_ref_path(key, ref), f"{int(revid)}\n")


def _read_history(key: str) -> list[dict]:
    path = _history_path(key)
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"Error: invalid JSON in {path}:{lineno}: {e}", file=sys.stderr)
                    sys.exit(1)
                if isinstance(item, dict):
                    entries.append(item)
    except FileNotFoundError:
        return []
    return entries


def _write_history(key: str, entries: list[dict]) -> bool:
    seen = {}
    for entry in entries:
        revid = entry.get("revid")
        if revid is not None:
            rid = int(revid)
            seen[rid] = {**seen.get(rid, {}), **entry}
    ordered = sorted(seen.values(), key=lambda e: (e.get("timestamp", ""), int(e["revid"])))
    content = "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n"
                      for e in ordered)
    return _atomic_write(_history_path(key), content)


def _revision_record(key: str, art: dict, result: dict, api_base: str) -> dict:
    revid = int(result["revid"])
    record = {
        "revid": revid,
        "parentid": int(result.get("parentid") or 0),
        "timestamp": result.get("timestamp", ""),
        "user": result.get("user", ""),
        "comment": result.get("comment", ""),
        "sha1": result.get("sha1", ""),
        "size": int(result.get("size") or len(result.get("wikitext", ""))),
        "title": art.get("title", key),
        "article_key": key,
        "url": art.get("url", ""),
        "api_base": api_base,
        "meta": f"{revid}.json",
        "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "contentmodel": result.get("contentmodel", ""),
        "contentformat": result.get("contentformat", ""),
    }
    if "wikitext" in result:
        record["body"] = f"{revid}.mw"
    return record


def _history_entry(record: dict) -> dict:
    keys = (
        "revid", "parentid", "timestamp", "user", "comment", "sha1", "size",
        "body", "meta",
    )
    return {k: record[k] for k in keys if k in record}


def _cache_revision(key: str, art: dict, result: dict, api_base: str) -> bool:
    revid = int(result["revid"])
    body_path = _revision_body_path(key, revid)
    meta_path = _revision_meta_path(key, revid)
    record = _revision_record(key, art, result, api_base)

    if os.path.exists(body_path):
        existing_meta = {}
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                existing_meta = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Warning: could not read {meta_path}: {e}", file=sys.stderr)
        if existing_meta.get("sha1") and existing_meta.get("sha1") != record.get("sha1"):
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            conflict = os.path.join(_cache_dir(key), f"{revid}.refetch-{stamp}.mw")
            if not _atomic_write(conflict, result["wikitext"]):
                return False
            print(
                f"Warning: cached revision {revid} metadata differs; wrote {conflict}",
                file=sys.stderr,
            )
            return False
        elif not existing_meta and not _write_json(meta_path, record):
            return False
    else:
        if not _atomic_write(body_path, result["wikitext"]):
            return False
        if not _write_json(meta_path, record):
            return False

    history = _read_history(key)
    history.append(_history_entry(record))
    return _write_history(key, history)


def _cache_revision_metadata(key: str, art: dict, rev: dict, api_base: str) -> bool:
    record = _revision_record(key, art, rev, api_base)
    meta_path = _revision_meta_path(key, record["revid"])
    if not os.path.exists(meta_path) and not _write_json(meta_path, record):
        return False
    history = _read_history(key)
    history.append(_history_entry(record))
    return _write_history(key, history)


def _resolve_cached_revid(key: str, spec: str | None = None) -> int:
    if spec in (None, "", "upstream"):
        revid = _read_ref(key, "upstream")
        if revid is not None:
            return revid
        history = _read_history(key)
        if history:
            return int(history[-1]["revid"])
        print(f"Error: no upstream revision cached for '{key}'. Run 'mwsync.py fetch {key}'.",
              file=sys.stderr)
        sys.exit(1)

    if spec.isdigit():
        return int(spec)

    base = spec
    offset = 0
    if "~" in spec:
        base, raw_offset = spec.rsplit("~", 1)
        try:
            offset = int(raw_offset)
        except ValueError:
            print(f"Error: invalid revision expression: {spec}", file=sys.stderr)
            sys.exit(1)
    elif spec.endswith("^"):
        base = spec[:-1]
        offset = 1

    revid = _resolve_cached_revid(key, base)
    if offset == 0:
        return revid

    history = _read_history(key)
    revids = [int(entry["revid"]) for entry in history]
    try:
        idx = revids.index(revid)
    except ValueError:
        print(f"Error: revision {revid} is not in {_history_path(key)}", file=sys.stderr)
        sys.exit(1)
    target = idx - offset
    if target < 0:
        print(f"Error: revision expression '{spec}' is older than cached history.",
              file=sys.stderr)
        sys.exit(1)
    return revids[target]


def _cached_body_or_die(key: str, revid: int) -> str:
    path = _revision_body_path(key, revid)
    if not os.path.exists(path):
        print(f"Error: cached body not found: {path}", file=sys.stderr)
        print(f"Fetch that revision before using it: mwsync.py fetch {key}", file=sys.stderr)
        sys.exit(1)
    return path


def _ensure_cached_body(key: str, art: dict, revid: int, api_base: str) -> str:
    path = _revision_body_path(key, revid)
    if os.path.exists(path):
        return path
    print(f"# Fetching body for revid {revid}...", file=sys.stderr)
    try:
        result = _fetch_revision_by_revid(revid, api_base)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not _cache_revision(key, art, result, api_base):
        sys.exit(1)
    return _cached_body_or_die(key, revid)


def _resolve_revision_arg(config: dict, spec: str, *, fetch_missing: bool = True) -> tuple[str, str]:
    if "@" not in spec:
        articles = config.get("wiki", {}).get("articles", {})
        if spec in articles:
            local = articles[spec].get("local", spec + ".mw")
            return local, f"{local} (local)"
        matches = [
            (key, art)
            for key, art in articles.items()
            if art.get("local", key + ".mw") == spec
        ]
        if len(matches) == 1:
            key, art = matches[0]
            local = art.get("local", key + ".mw")
            return local, f"{local} (local)"
        if len(matches) > 1:
            print(f"Error: local filename '{spec}' matches multiple articles.", file=sys.stderr)
            sys.exit(1)
        if os.path.exists(spec):
            return spec, spec
        resolve_article_entry(config, spec)

    article, revspec = spec.split("@", 1)
    key, art = resolve_article_entry(config, article)
    _check_legacy_cache(key)
    revid = _resolve_cached_revid(key, revspec)
    if fetch_missing:
        path = _ensure_cached_body(key, art, revid, get_api_base(config))
    else:
        path = _cached_body_or_die(key, revid)
    return path, f"{key}@{revspec} ({revid})"


def _git_is_modified(path: str) -> bool | None:
    """Return True if file has uncommitted changes, False if clean, None if not in git."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "--", path],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if res.returncode != 0:
            return None
        return bool(res.stdout.strip())
    except FileNotFoundError:
        return None


def _file_content_matches(path: str, content: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read() == content
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _run_merge_file(local: str, base: str, upstream: str) -> tuple[int, str, str]:
    cmd = [
        "git", "merge-file", "-p",
        "-L", f"{local} (local)",
        "-L", f"{base} (base)",
        "-L", f"{upstream} (upstream)",
        local, base, upstream,
    ]
    res = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return res.returncode, res.stdout, res.stderr


# ---------------------------------------------------------------------------
# Subcommand runners
# ---------------------------------------------------------------------------

def run_init(args, config_path: str) -> None:
    if os.path.exists(config_path):
        print(f"Error: config file already exists: {config_path}", file=sys.stderr)
        sys.exit(1)
    config = minimal_config()
    if not save_config(config, config_path):
        sys.exit(1)
    print(f"Created {config_path}", file=sys.stderr)


def run_add(args, config: dict, config_path: str) -> None:
    key, art, _created = _register_article_url(config, config_path, args.url)
    print(f"Registered '{key}'", file=sys.stderr)
    print(f"  title: {art.get('title', key)}", file=sys.stderr)
    print(f"  local: {art.get('local', key + '.mw')}", file=sys.stderr)
    print(f"Run: mwsync.py fetch {key}", file=sys.stderr)


def run_checkout(args, config: dict, config_path: str) -> None:
    target = args.target
    depth = max(1, int(getattr(args, "depth", DEFAULT_HISTORY_DEPTH) or 1))
    to_path = getattr(args, "to", None)

    if "@" in target:
        if not to_path:
            print("Error: checkout ARTICLE@REV requires --to PATH.", file=sys.stderr)
            sys.exit(1)
        source, label = _resolve_revision_arg(config, target)
        text = _read_text(source)
        if not _atomic_write(to_path, text):
            sys.exit(1)
        print(f"# Wrote {label} to {to_path}", file=sys.stderr)
        return

    if to_path:
        print("Error: --to is only supported with ARTICLE@REV checkout.", file=sys.stderr)
        sys.exit(1)

    if _looks_like_article_url(target):
        key, art, created = _register_article_url(
            config, config_path, target, allow_existing=True,
        )
        if created:
            print(f"# Registered '{key}'", file=sys.stderr)
        else:
            print(f"# Article '{key}' already registered", file=sys.stderr)
    else:
        key, art = resolve_article_entry(config, target)

    fetch_args = argparse.Namespace(article=key, dry_run=False, depth=depth)
    run_fetch(fetch_args, config, config_path)
    merge_args = argparse.Namespace(article=key)
    run_merge(merge_args, config, config_path)


def run_fetch(args, config: dict, config_path: str) -> None:
    key, art = resolve_article_entry(config, args.article)
    _check_legacy_cache(key)
    title = art.get("title", key)
    local = art.get("local", key + ".mw")
    api_base = get_api_base(config)
    dry_run = getattr(args, "dry_run", False)
    all_known = getattr(args, "all_known", False)
    with_bodies = getattr(args, "with_bodies", False)
    depth_arg = getattr(args, "depth", DEFAULT_HISTORY_DEPTH)
    depth = None if all_known else max(1, int(depth_arg or 1))

    if dry_run:
        depth_label = "all available" if all_known else str(depth)
        print(f"# Fetch plan for: {key}", file=sys.stderr)
        print(f"#   Title:    {title}", file=sys.stderr)
        print(f"#   API:      {api_base}", file=sys.stderr)
        print(f"#   Local:    {local} (unchanged)", file=sys.stderr)
        print(f"#   Cache:    {_cache_dir(key)}", file=sys.stderr)
        print(f"#   Depth:    {depth_label} metadata revision(s)", file=sys.stderr)
        print(f"#   Bodies:   {'all fetched metadata revisions' if with_bodies else 'latest only'}",
              file=sys.stderr)
        prev = art.get("upstream_revid")
        if prev:
            print(f"#   Current upstream_revid: {prev}", file=sys.stderr)
        return

    print(f"# Fetching '{title}' from {api_base}...", file=sys.stderr)
    try:
        result = _fetch_page(title, api_base)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    revid = result["revid"]
    wikitext = result["wikitext"]
    print(f"# Got revid {revid} ({len(wikitext)} chars)", file=sys.stderr)

    if not _cache_revision(key, art, result, api_base):
        sys.exit(1)
    if all_known or depth > 1:
        if all_known:
            print("# Fetching metadata for all available revisions...", file=sys.stderr)
        else:
            print(f"# Fetching metadata for newest {depth} revisions...", file=sys.stderr)
        try:
            for rev in _fetch_revision_metadata(title, api_base, depth):
                if not _cache_revision_metadata(key, art, rev, api_base):
                    sys.exit(1)
                if with_bodies and int(rev.get("revid") or 0) != int(revid):
                    _ensure_cached_body(key, art, int(rev["revid"]), api_base)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    if not _write_ref(key, "upstream", int(revid)):
        sys.exit(1)
    print(f"# Cached revision {_revision_body_path(key, revid)}", file=sys.stderr)
    print(f"# Updated refs/upstream to {revid}", file=sys.stderr)
    print(f"# Left {local} unchanged; run 'mwsync.py merge {key}' to update it.",
          file=sys.stderr)


def run_push(args, config: dict, config_path: str) -> None:
    key, art = resolve_article_entry(config, args.article)
    _check_legacy_cache(key)
    title = art.get("title", key)
    local = art.get("local", key + ".mw")
    api_base = get_api_base(config)
    baserevid = _read_ref(key, "base") or art.get("upstream_revid", 0)
    dry_run = getattr(args, "dry_run", False)
    message = getattr(args, "message", None)
    create_new = getattr(args, "new", False)

    if not baserevid and not create_new:
        print(f"Error: upstream_revid not set for '{key}'.", file=sys.stderr)
        print(f"If this is a new article, use 'mwsync.py push --new {key}' to create it.",
              file=sys.stderr)
        print(f"Otherwise, run 'mwsync.py fetch {key}' first.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(local):
        print(f"Error: local file not found: {local}", file=sys.stderr)
        print(f"Run 'mwsync.py fetch {key}' first.", file=sys.stderr)
        sys.exit(1)

    username = os.environ.get("MWSYNC_MW_USER", "")
    password = os.environ.get("MWSYNC_MW_PASSWORD", "")

    if dry_run:
        try:
            page_len = len(open(local, encoding="utf-8").read())
        except Exception:
            page_len = 0
        print(f"# Push plan for: {key}", file=sys.stderr)
        print(f"#   Title:      {title}", file=sys.stderr)
        print(f"#   API:        {api_base}", file=sys.stderr)
        print(f"#   Local:      {local} ({page_len} chars)", file=sys.stderr)
        if create_new:
            print("#   Mode:       CREATE NEW article", file=sys.stderr)
        else:
            print(f"#   Base revid: {baserevid}", file=sys.stderr)
        if message:
            print(f"#   Summary:    {message}", file=sys.stderr)
        else:
            print("#   Summary:    (editor will open)", file=sys.stderr)
        if username:
            print(f"#   Credentials: found (user: {username})", file=sys.stderr)
        else:
            print("#   Credentials: not set (MWSYNC_MW_USER / MWSYNC_MW_PASSWORD)",
                  file=sys.stderr)
        return

    if not username or not password:
        print("Error: push requires credentials.", file=sys.stderr)
        print("Set MWSYNC_MW_USER and MWSYNC_MW_PASSWORD environment variables.",
              file=sys.stderr)
        sys.exit(1)

    try:
        with open(local, "r", encoding="utf-8") as f:
            page_text = f.read()
    except Exception as e:
        print(f"Error reading {local}: {e}", file=sys.stderr)
        sys.exit(1)

    if message:
        summary = message
    else:
        summary = _edit_summary("", key, title, baserevid)
        if summary is None:
            print("# Aborted: empty edit summary.", file=sys.stderr)
            sys.exit(0)

    print(f"# Pushing '{key}'...", file=sys.stderr)
    print(f"#   Title:      {title}", file=sys.stderr)
    print(f"#   Content:    {len(page_text)} chars", file=sys.stderr)
    if create_new:
        print("#   Mode:       CREATE NEW article", file=sys.stderr)
    else:
        print(f"#   Base revid: {baserevid}", file=sys.stderr)
    print(f"#   Summary:    {summary}", file=sys.stderr)

    print(f"# Logging in as {username}...", file=sys.stderr)
    try:
        opener = _mw_login(api_base, username, password)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("# Getting CSRF token...", file=sys.stderr)
    try:
        csrf_token = _mw_get_csrf_token(api_base, opener)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("# Submitting edit...", file=sys.stderr)
    try:
        new_revid = _mw_edit_page(api_base, opener, title, page_text,
                                   baserevid, csrf_token, summary,
                                   create_new=create_new)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"# Success! New revid: {new_revid}", file=sys.stderr)

    now_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wiki = config.setdefault("wiki", {})
    articles = wiki.setdefault("articles", {})
    art = articles.setdefault(key, {})
    art["last_pushed_revid"] = new_revid
    art["last_pushed_at"] = now_utc
    if not _write_ref(key, "last-pushed", int(new_revid)):
        sys.exit(1)
    save_config(config, config_path)

    # Auto-fetch to resync upstream refs with the revision we just created.
    print("# Re-fetching to sync upstream cache...", file=sys.stderr)
    try:
        result = _fetch_page(title, api_base)
        if not _cache_revision(key, art, result, api_base):
            sys.exit(1)
        if not _write_ref(key, "upstream", int(result["revid"])):
            sys.exit(1)
        if not _write_ref(key, "base", int(result["revid"])):
            sys.exit(1)
        _atomic_write(local, result["wikitext"])
        art["upstream_revid"] = result["revid"]
        art["upstream_timestamp"] = result["timestamp"]
        art["upstream_editor"] = result["user"]
        art["upstream_summary"] = result["comment"]
        art["upstream_sha1"] = result["sha1"]
        save_config(config, config_path)
        print(f"# Synced upstream_revid={result['revid']}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: auto-fetch failed: {e}", file=sys.stderr)


def run_diff(args, config: dict, config_path: str) -> None:
    left_arg = args.left
    right_arg = args.right
    key, art = resolve_article_entry(config, left_arg.split("@", 1)[0])
    _check_legacy_cache(key)
    local = art.get("local", key + ".mw")

    if getattr(args, "remote", False):
        title = art.get("title", key)
        api_base = get_api_base(config)
        print(f"# Re-fetching upstream cache for '{key}'...", file=sys.stderr)
        try:
            result = _fetch_page(title, api_base)
            if not _cache_revision(key, art, result, api_base):
                sys.exit(1)
            if not _write_ref(key, "upstream", int(result["revid"])):
                sys.exit(1)
            print(f"# Got revid {result['revid']}", file=sys.stderr)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    if right_arg is None:
        left = f"{key}@upstream"
        right = local
    else:
        left = left_arg
        right = right_arg

    left_path, _left_label = _resolve_revision_arg(config, left)
    right_path, _right_label = _resolve_revision_arg(config, right)
    res = subprocess.run(["git", "diff", "--no-index", left_path, right_path])
    if res.returncode not in (0, 1):
        sys.exit(res.returncode)


def run_difftool(args, config: dict, config_path: str) -> None:
    key, art = resolve_article_entry(config, args.article)
    _check_legacy_cache(key)
    local = art.get("local", key + ".mw")
    revid = _resolve_cached_revid(key, "upstream")
    snapshot = _cached_body_or_die(key, revid)

    subprocess.run(["meld", snapshot, local])


def run_merge(args, config: dict, config_path: str) -> None:
    key, art = resolve_article_entry(config, args.article)
    _check_legacy_cache(key)
    local = art.get("local", key + ".mw")
    api_base = get_api_base(config)

    upstream_revid = _read_ref(key, "upstream")
    if upstream_revid is None:
        print(f"Error: no upstream revision cached for '{key}'.", file=sys.stderr)
        print(f"Run 'mwsync.py fetch {key}' first.", file=sys.stderr)
        sys.exit(1)

    base_revid = _read_ref(key, "base")
    upstream_path = _ensure_cached_body(key, art, upstream_revid, api_base)
    upstream_text = _read_text(upstream_path)

    if not os.path.exists(local):
        if not _atomic_write(local, upstream_text):
            sys.exit(1)
        if not _write_ref(key, "base", upstream_revid):
            sys.exit(1)
        print(f"# Checked out {local} at upstream revid {upstream_revid}", file=sys.stderr)
        print(f"# Updated refs/base to {upstream_revid}", file=sys.stderr)
        return

    if base_revid is None:
        if _file_content_matches(local, upstream_text):
            if not _write_ref(key, "base", upstream_revid):
                sys.exit(1)
            print(f"# Adopted existing {local} as refs/base {upstream_revid}", file=sys.stderr)
            return
        print(f"Error: no base revision cached for '{key}'.", file=sys.stderr)
        print(f"Run 'mwsync.py fetch {key}' before making local edits.", file=sys.stderr)
        sys.exit(1)

    base_path = _cached_body_or_die(key, base_revid)
    base_text = _read_text(base_path)

    if int(base_revid) == int(upstream_revid):
        print(f"# Already up to date at revid {upstream_revid}", file=sys.stderr)
        return

    if _file_content_matches(local, upstream_text):
        if not _write_ref(key, "base", upstream_revid):
            sys.exit(1)
        print(f"# Local file already matches upstream revid {upstream_revid}", file=sys.stderr)
        print(f"# Updated refs/base to {upstream_revid}", file=sys.stderr)
        return

    if _file_content_matches(local, base_text):
        if not _atomic_write(local, upstream_text):
            sys.exit(1)
        if not _write_ref(key, "base", upstream_revid):
            sys.exit(1)
        print(f"# Fast-forwarded {local} from {base_revid} to {upstream_revid}", file=sys.stderr)
        return

    code, merged_text, merge_stderr = _run_merge_file(local, base_path, upstream_path)
    if code == 0:
        if not _atomic_write(local, merged_text):
            sys.exit(1)
        if not _write_ref(key, "base", upstream_revid):
            sys.exit(1)
        print(f"# Merged upstream revid {upstream_revid} into {local}", file=sys.stderr)
        print(f"# Updated refs/base to {upstream_revid}", file=sys.stderr)
        return

    if code == 1:
        if not _atomic_write(local, merged_text):
            sys.exit(1)
        print(f"Conflict: merged with conflict markers in {local}", file=sys.stderr)
        print(f"Resolve conflicts, then commit locally. refs/base remains {base_revid}.",
              file=sys.stderr)
        sys.exit(1)

    if merge_stderr:
        print(merge_stderr.rstrip(), file=sys.stderr)
    print("Error: git merge-file failed.", file=sys.stderr)
    sys.exit(1)


def run_log(args, config: dict, config_path: str) -> None:
    key, art = resolve_article_entry(config, args.article)
    _check_legacy_cache(key)
    history = _read_history(key)
    if not history:
        print(f"No cached history for '{key}'. Run 'mwsync.py fetch {key}'.")
        return
    for entry in reversed(history):
        revid = entry.get("revid", "")
        ts = entry.get("timestamp", "")
        user = entry.get("user", "")
        comment = entry.get("comment", "")
        print(f"{revid}  {ts}  {user}")
        if comment:
            print(f"  {comment}")
    cached_revids = {int(entry["revid"]) for entry in history if entry.get("revid")}
    missing_parents = [
        int(entry.get("parentid") or 0)
        for entry in history
        if int(entry.get("parentid") or 0) and int(entry.get("parentid") or 0) not in cached_revids
    ]
    if missing_parents:
        shown = ", ".join(str(parent) for parent in missing_parents[:5])
        suffix = " ..." if len(missing_parents) > 5 else ""
        print(f"... history incomplete; missing parent revision(s): {shown}{suffix}")
        print(f"... fetch a deeper window with: mwsync.py fetch --depth N {key}")


def run_show(args, config: dict, config_path: str) -> None:
    spec = args.revision
    if "@" not in spec:
        print("Error: show expects ARTICLE@REV, for example New_York@upstream.",
              file=sys.stderr)
        sys.exit(1)
    article, revspec = spec.split("@", 1)
    key, art = resolve_article_entry(config, article)
    _check_legacy_cache(key)
    api_base = get_api_base(config)
    revid = _resolve_cached_revid(key, revspec)
    path = _ensure_cached_body(key, art, revid, api_base)
    with open(path, "r", encoding="utf-8") as f:
        sys.stdout.write(f.read())


def _fsck_article(key: str, art: dict) -> int:
    issues = 0
    if _legacy_cache_exists(key):
        print(f"{key}: legacy cache detected: {_server_snapshot_path(key)}")
        issues += 1

    history = _read_history(key)
    seen_revids = set()
    previous_key = None
    for entry in history:
        raw_revid = entry.get("revid")
        try:
            revid = int(raw_revid)
        except (TypeError, ValueError):
            print(f"{key}: invalid history revid: {raw_revid!r}")
            issues += 1
            continue

        if revid in seen_revids:
            print(f"{key}: duplicate history revid: {revid}")
            issues += 1
        seen_revids.add(revid)

        sort_key = (entry.get("timestamp", ""), revid)
        if previous_key and sort_key < previous_key:
            print(f"{key}: history is not chronological near revid {revid}")
            issues += 1
        previous_key = sort_key

        meta_name = entry.get("meta")
        if meta_name:
            meta_path = os.path.join(_cache_dir(key), meta_name)
            if not os.path.exists(meta_path):
                print(f"{key}: missing metadata sidecar for revid {revid}: {meta_path}")
                issues += 1
            else:
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    if int(meta.get("revid") or 0) != revid:
                        print(f"{key}: metadata revid mismatch in {meta_path}")
                        issues += 1
                    if entry.get("sha1") and meta.get("sha1") and entry["sha1"] != meta["sha1"]:
                        print(f"{key}: sha1 mismatch between history and {meta_path}")
                        issues += 1
                except Exception as e:
                    print(f"{key}: cannot read metadata sidecar {meta_path}: {e}")
                    issues += 1

        body_name = entry.get("body")
        if body_name:
            body_path = os.path.join(_cache_dir(key), body_name)
            if not os.path.exists(body_path):
                print(f"{key}: missing cached body for revid {revid}: {body_path}")
                issues += 1

    for ref in ("upstream", "base", "last-pushed"):
        ref_path = _ref_path(key, ref)
        if not os.path.exists(ref_path):
            continue
        try:
            with open(ref_path, "r", encoding="utf-8") as f:
                revid = int(f.read().strip())
        except Exception as e:
            print(f"{key}: invalid refs/{ref}: {e}")
            issues += 1
            continue
        if history and revid not in seen_revids:
            print(f"{key}: refs/{ref} points outside history: {revid}")
            issues += 1
        if ref in ("upstream", "base") and not os.path.exists(_revision_body_path(key, revid)):
            print(f"{key}: refs/{ref} body is missing: {_revision_body_path(key, revid)}")
            issues += 1

    upstream_path = _ref_path(key, "upstream")
    if history and os.path.exists(upstream_path):
        try:
            with open(upstream_path, "r", encoding="utf-8") as f:
                upstream_ref = int(f.read().strip())
            if int(history[-1]["revid"]) != int(upstream_ref):
                print(f"{key}: refs/upstream ({upstream_ref}) does not match latest history "
                      f"({history[-1]['revid']})")
                issues += 1
        except Exception:
            pass

    if issues == 0:
        print(f"{key}: ok")
    return issues


def run_fsck(args, config: dict, config_path: str) -> None:
    articles = config.get("wiki", {}).get("articles", {})
    if not articles:
        print("No articles registered.")
        return

    if getattr(args, "article", None):
        key, art = resolve_article_entry(config, args.article)
        items = [(key, art)]
    else:
        items = list(articles.items())

    issues = 0
    for key, art in items:
        issues += _fsck_article(key, art)
    if issues:
        print(f"fsck found {issues} issue(s).", file=sys.stderr)
        sys.exit(1)


def run_status(args, config: dict, config_path: str) -> None:
    articles = config.get("wiki", {}).get("articles", {})
    if not articles:
        print("No articles registered. Use 'mwsync.py add URL' to add one.")
        return

    key_filter = getattr(args, "article", None)
    if key_filter:
        key, art = resolve_article_entry(config, key_filter)
        items = [(key, art)]
    else:
        items = list(articles.items())

    for key, art in items:
        local = art.get("local", key + ".mw")
        upstream_ref = _read_ref(key, "upstream")
        base_ref = _read_ref(key, "base")
        last_pushed_ref = _read_ref(key, "last-pushed")
        history = _read_history(key)
        latest = {}
        if upstream_ref is not None:
            matches = [entry for entry in history
                       if int(entry.get("revid") or 0) == int(upstream_ref)]
            if matches:
                latest = matches[-1]
        elif history:
            latest = history[-1]
        revid = art.get("upstream_revid", "") or upstream_ref or ""
        ts = art.get("upstream_timestamp", "") or latest.get("timestamp", "")
        editor = art.get("upstream_editor", "") or latest.get("user", "")
        pushed_revid = art.get("last_pushed_revid", "")
        pushed_at = art.get("last_pushed_at", "")

        modified = _git_is_modified(local)
        if modified is True:
            flag = "[modified]"
        elif modified is False:
            flag = "[clean]"
        else:
            flag = ""

        print(key)
        print(f"  local:           {local}  {flag}".rstrip())
        if revid:
            rev_info = str(revid)
            if ts:
                rev_info += f"  ({ts}"
                if editor:
                    rev_info += f" by {editor}"
                rev_info += ")"
            print(f"  upstream_revid:  {rev_info}")
        else:
            print("  upstream_revid:  (not fetched)")
        if upstream_ref:
            print(f"  refs/upstream:   {upstream_ref}")
        if base_ref:
            print(f"  refs/base:       {base_ref}")
        if last_pushed_ref:
            print(f"  refs/last-pushed:{last_pushed_ref}")
        if pushed_revid:
            print(f"  last_pushed:     {pushed_revid}  ({pushed_at})")
        else:
            print("  last_pushed:     (never)")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="mwsync.py",
        description="Sync individual MediaWiki articles to and from local .mw files.",
        epilog=(
            "Credentials for push: set MWSYNC_MW_USER and MWSYNC_MW_PASSWORD "
            "environment variables."
        ),
    )
    ap.add_argument(
        "--config", default=DEFAULT_CONFIG_PATH,
        help=f"Path to config file (default: {DEFAULT_CONFIG_PATH})",
    )
    sub = ap.add_subparsers(dest="subcommand", help="Available subcommands")

    # init
    sub.add_parser("init", help="Create a minimal mwsync.yaml")

    # add
    p_add = sub.add_parser("add", help="Register a new article by URL")
    p_add.add_argument("url", metavar="URL", help="Full wiki page URL")

    # checkout
    p_checkout = sub.add_parser("checkout",
                                help="Register, fetch, and merge an article")
    p_checkout.add_argument("target", metavar="URL_OR_ARTICLE_OR_REV",
                            help="Full wiki page URL, registered article key, or ARTICLE@REV")
    p_checkout.add_argument("--depth", type=int, default=DEFAULT_HISTORY_DEPTH,
                            help=(f"Fetch metadata for the newest N revisions "
                                  f"(default: {DEFAULT_HISTORY_DEPTH})"))
    p_checkout.add_argument("--to", metavar="PATH",
                            help="Write ARTICLE@REV to PATH without changing refs")

    # fetch
    p_fetch = sub.add_parser("fetch", help="Pull current wikitext and metadata into _cache")
    p_fetch.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")
    p_fetch.add_argument("--dry-run", action="store_true", help="Preview without writing")
    p_fetch.add_argument("--depth", type=int, default=DEFAULT_HISTORY_DEPTH,
                         help=(f"Fetch metadata for the newest N revisions "
                               f"(default: {DEFAULT_HISTORY_DEPTH})"))
    p_fetch.add_argument("--all-known", action="store_true",
                         help="Fetch metadata for all available revisions")
    p_fetch.add_argument("--with-bodies", action="store_true",
                         help="Also fetch bodies for revisions in the metadata window")

    # push
    p_push = sub.add_parser("push", help="Submit local edits back to the wiki")
    p_push.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")
    p_push.add_argument("--dry-run", action="store_true", help="Preview without pushing")
    p_push.add_argument("--new", action="store_true",
                        help="Create a new article (instead of editing an existing one)")
    p_push.add_argument("-m", "--message", help="Edit summary (skips editor prompt)")

    # diff
    p_diff = sub.add_parser("diff", help="Compare cached revisions and local files")
    p_diff.add_argument("left", metavar="LEFT",
                        help="Article key/local file, or ARTICLE@REV")
    p_diff.add_argument("right", metavar="RIGHT", nargs="?",
                        help="Optional article key/local file, or ARTICLE@REV")
    p_diff.add_argument("--remote", action="store_true",
                        help="Re-fetch upstream cache before diffing")

    # difftool
    p_difftool = sub.add_parser("difftool",
                                help="Launch meld to compare upstream cache vs local")
    p_difftool.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")

    # merge
    p_merge = sub.add_parser("merge", help="Merge fetched upstream changes into local file")
    p_merge.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")

    # log
    p_log = sub.add_parser("log", help="Show cached revision history")
    p_log.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")

    # show
    p_show = sub.add_parser("show", help="Print cached revision text")
    p_show.add_argument("revision", metavar="ARTICLE@REV",
                        help="Revision expression, e.g. New_York@upstream")

    # fsck
    p_fsck = sub.add_parser("fsck", help="Check cache refs, history, and revision files")
    p_fsck.add_argument("article", metavar="ARTICLE", nargs="?",
                        help="Article key (omit to check all)")

    # status
    p_status = sub.add_parser("status", help="Show sync state of tracked articles")
    p_status.add_argument("article", metavar="ARTICLE", nargs="?",
                          help="Article key (omit to show all)")

    args = ap.parse_args()

    if not args.subcommand:
        ap.print_help()
        sys.exit(0)

    config_path = args.config
    if args.subcommand == "init":
        run_init(args, config_path)
        return

    if (args.subcommand == "checkout"
            and not os.path.exists(config_path)
            and _looks_like_article_url(args.target)):
        config = minimal_config()
    else:
        config = load_config(config_path)

    if args.subcommand == "add":
        run_add(args, config, config_path)
    elif args.subcommand == "checkout":
        run_checkout(args, config, config_path)
    elif args.subcommand == "fetch":
        run_fetch(args, config, config_path)
    elif args.subcommand == "push":
        run_push(args, config, config_path)
    elif args.subcommand == "diff":
        run_diff(args, config, config_path)
    elif args.subcommand == "difftool":
        run_difftool(args, config, config_path)
    elif args.subcommand == "merge":
        run_merge(args, config, config_path)
    elif args.subcommand == "log":
        run_log(args, config, config_path)
    elif args.subcommand == "show":
        run_show(args, config, config_path)
    elif args.subcommand == "fsck":
        run_fsck(args, config, config_path)
    elif args.subcommand == "status":
        run_status(args, config, config_path)


if __name__ == "__main__":
    main()
