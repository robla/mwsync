#!/usr/bin/env python3
# Copyright (c) 2026 Rob Lanphier and contributors
# SPDX-License-Identifier: MIT
# See LICENSE for details.
"""
mwsync.py — Per-article local ↔ MediaWiki sync tool.

Subcommands:
  init      Create a minimal mwsync.yaml
  add       Register a new article by URL or page name
  checkout  Register, fetch, and merge an article into a local .mw file
  fetch     Pull current wikitext and metadata into _cache
  commit    Snapshot local edits as a pending wiki edit
  push      Submit pending local commits back to the wiki
  diff      Compare upstream cache vs working local file
  difftool  Launch meld to compare upstream cache vs working local
  merge     Merge fetched upstream changes into local file
  restore   Restore the local .mw file from refs/base
  log       Show cached revision history
  show      Print cached revision text
  fsck      Check cache refs, history, and revision files
  migrate   Update legacy article entries to the current namespace layout
  status    Show sync state of tracked articles

Usage:
  mwsync.py init
  mwsync.py add Maine
  mwsync.py checkout https://electowiki.org/wiki/Maine
  mwsync.py checkout Maine@upstream^ --to scratch/Maine-old.mw
  mwsync.py fetch Maine
  mwsync.py diff Maine
  mwsync.py diff Maine@upstream^ Maine@upstream
  mwsync.py merge Maine
  mwsync.py restore Maine
  mwsync.py commit Maine -m "Update Maine article"
  mwsync.py push Maine
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
import shutil
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
NAMESPACE_CACHE_PATH = os.path.join("_cache", "_titles", "namespaces.json")

FALLBACK_NAMESPACES = {
    "0": {"canonical": "", "local": ""},
    "1": {"canonical": "Talk", "local": "Talk"},
    "2": {"canonical": "User", "local": "User"},
    "3": {"canonical": "User talk", "local": "User talk"},
    "4": {"canonical": "Project", "local": "Project"},
    "5": {"canonical": "Project talk", "local": "Project talk"},
    "6": {"canonical": "File", "local": "File"},
    "7": {"canonical": "File talk", "local": "File talk"},
    "8": {"canonical": "MediaWiki", "local": "MediaWiki"},
    "9": {"canonical": "MediaWiki talk", "local": "MediaWiki talk"},
    "10": {"canonical": "Template", "local": "Template"},
    "11": {"canonical": "Template talk", "local": "Template talk"},
    "12": {"canonical": "Help", "local": "Help"},
    "13": {"canonical": "Help talk", "local": "Help talk"},
    "14": {"canonical": "Category", "local": "Category"},
    "15": {"canonical": "Category talk", "local": "Category talk"},
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    if yaml is None:
        print("Error: pyyaml is not installed. Run: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(path):
        print(f"Error: config file not found: {path}", file=sys.stderr)
        print("Run 'mwsync.py init' first, or run mwsync.py from a directory "
              "that has already been initialized.",
              file=sys.stderr)
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


def _normalize_dbkey(value: str) -> str:
    parts = value.replace("_", " ").strip().split()
    return "_".join(parts)


def _title_from_dbkey(dbkey: str) -> str:
    return dbkey.replace("_", " ")


def _namespace_segment(name: str, namespace_id: int) -> str:
    dbkey = _normalize_dbkey(name)
    return dbkey or f"ns_{int(namespace_id):02d}"


def _encode_dbkey_segment(dbkey: str) -> str:
    return _normalize_dbkey(dbkey).replace("/", "__")


def _namespace_local_dir(namespace_name: str, namespace_id: int) -> str:
    ns_id = int(namespace_id)
    width = 2 if ns_id < 100 else len(str(ns_id))
    return f"{ns_id:0{width}d}ns_" + _namespace_segment(namespace_name, namespace_id)


def _fallback_namespace_map(api_base: str = "") -> dict:
    aliases = {}
    for raw_id, ns in FALLBACK_NAMESPACES.items():
        ns_id = int(raw_id)
        for name in (ns.get("canonical", ""), ns.get("local", "")):
            if name:
                aliases[name.casefold()] = ns_id
    return {
        "fetched_at": "",
        "api_base": api_base,
        "namespaces": FALLBACK_NAMESPACES,
        "aliases": aliases,
        "source": "fallback",
    }


def _normalize_namespace_map(raw: dict, api_base: str) -> dict:
    namespaces = {}
    aliases = {}

    raw_namespaces = raw.get("namespaces", {})
    if isinstance(raw_namespaces, list):
        ns_items = []
        for item in raw_namespaces:
            if isinstance(item, dict):
                ns_items.append((str(item.get("id", "")), item))
    else:
        ns_items = list(raw_namespaces.items()) if isinstance(raw_namespaces, dict) else []

    for raw_id, item in ns_items:
        try:
            ns_id = int(raw_id)
        except (TypeError, ValueError):
            try:
                ns_id = int(item.get("id"))
            except (TypeError, ValueError, AttributeError):
                continue
        if not isinstance(item, dict):
            continue
        local = item.get("local")
        if local is None:
            local = item.get("name")
        if local is None:
            local = item.get("*", "")
        canonical = item.get("canonical", "")
        if ns_id == 0:
            local = ""
            canonical = ""
        namespaces[str(ns_id)] = {
            "canonical": canonical or local or "",
            "local": local or canonical or "",
        }
        for name in (canonical, local):
            if name:
                aliases[str(name).casefold()] = ns_id

    raw_aliases = raw.get("aliases", {})
    if isinstance(raw_aliases, dict):
        for alias, ns_id in raw_aliases.items():
            try:
                aliases[str(alias).casefold()] = int(ns_id)
            except (TypeError, ValueError):
                continue
    elif isinstance(raw_aliases, list):
        for item in raw_aliases:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias") or item.get("*")
            try:
                ns_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if alias:
                aliases[str(alias).casefold()] = ns_id

    return {
        "fetched_at": raw.get("fetched_at", ""),
        "api_base": raw.get("api_base", api_base),
        "namespaces": namespaces,
        "aliases": aliases,
    }


def _fetch_namespace_map(api_base: str) -> dict:
    params = {
        "action": "query",
        "format": "json",
        "meta": "siteinfo",
        "siprop": "namespaces|namespacealiases",
        "formatversion": "2",
    }
    url = api_base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    query = data.get("query", {})
    if not query:
        raise ValueError("MediaWiki siteinfo response did not include query data")
    namespace_map = _normalize_namespace_map({
        "api_base": api_base,
        "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "namespaces": query.get("namespaces", {}),
        "aliases": query.get("namespacealiases", []),
    }, api_base)
    if not _write_json(NAMESPACE_CACHE_PATH, namespace_map):
        raise ValueError(f"failed to write {NAMESPACE_CACHE_PATH}")
    return namespace_map


def _load_namespace_map(config: dict, *, fetch: bool = True,
                        allow_fallback: bool = False) -> dict:
    api_base = get_api_base(config)
    read_error = None
    try:
        with open(NAMESPACE_CACHE_PATH, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("api_base") == api_base:
            return _normalize_namespace_map(cached, api_base)
    except FileNotFoundError:
        pass
    except Exception as e:
        read_error = e

    if fetch:
        try:
            return _fetch_namespace_map(api_base)
        except Exception as e:
            if allow_fallback:
                print(f"Warning: could not fetch namespace map: {e}", file=sys.stderr)
                return _fallback_namespace_map(api_base)
            print(f"Error: could not fetch namespace map from {api_base}: {e}",
                  file=sys.stderr)
            print("Namespace-aware commands need the target wiki namespace map.",
                  file=sys.stderr)
            sys.exit(1)
    if allow_fallback:
        if read_error is not None:
            print(f"Warning: could not read {NAMESPACE_CACHE_PATH}: {read_error}",
                  file=sys.stderr)
        return _fallback_namespace_map(api_base)
    if read_error is not None:
        print(f"Error: could not read {NAMESPACE_CACHE_PATH}: {read_error}", file=sys.stderr)
    else:
        print(f"Error: namespace map not found: {NAMESPACE_CACHE_PATH}", file=sys.stderr)
    print("Run a namespace-aware command while online, or run 'mwsync.py migrate' "
          "to update legacy entries.", file=sys.stderr)
    sys.exit(1)


def _namespace_name(namespace_map: dict, namespace_id: int) -> str:
    ns = namespace_map.get("namespaces", {}).get(str(int(namespace_id)), {})
    name = ns.get("local") or ns.get("canonical") or ""
    return name or f"ns_{int(namespace_id):02d}"


def _namespace_id_for_prefix(namespace_map: dict, prefix: str) -> int | None:
    return namespace_map.get("aliases", {}).get(prefix.casefold())


def _parse_title_parts(raw_title: str, namespace_map: dict) -> dict:
    raw = raw_title.strip()
    if not raw:
        print("Error: article name cannot be empty.", file=sys.stderr)
        sys.exit(1)

    title_text = raw.replace("_", " ")
    namespace_id = 0
    page_part = title_text
    if ":" in title_text:
        prefix, rest = title_text.split(":", 1)
        resolved = _namespace_id_for_prefix(namespace_map, prefix.strip())
        if resolved is not None and int(resolved) != 0:
            namespace_id = int(resolved)
            page_part = rest

    dbkey = _normalize_dbkey(page_part)
    namespace_name = "" if namespace_id == 0 else _namespace_name(namespace_map, namespace_id)
    title = _title_from_dbkey(dbkey)
    if namespace_id != 0:
        title = f"{namespace_name}:{title}"
    return {
        "title": title,
        "namespace": namespace_id,
        "namespace_name": namespace_name,
        "dbkey": dbkey,
    }


def _main_title_parts(raw_title: str) -> dict:
    dbkey = _normalize_dbkey(raw_title)
    return {
        "title": _title_from_dbkey(dbkey),
        "namespace": 0,
        "namespace_name": "",
        "dbkey": dbkey,
    }


def _legacy_non_main_entry(config: dict, key: str, art: dict) -> bool:
    if "namespace" in art or "dbkey" in art:
        return False
    return ":" in art.get("title", key) or ":" in key


def _fail_legacy_non_main(key: str, art: dict) -> None:
    print(f"Error: '{key}' is a legacy non-main-namespace entry.", file=sys.stderr)
    print(f"Title: {art.get('title', key)}", file=sys.stderr)
    print("Run 'mwsync.py migrate' to update namespace metadata and local paths.",
          file=sys.stderr)
    sys.exit(1)


def _key_for_title_parts(parts: dict) -> str:
    dbkey = parts["dbkey"]
    if not dbkey:
        print("Error: article title cannot be empty.", file=sys.stderr)
        sys.exit(1)
    key_dbkey = _encode_dbkey_segment(dbkey)
    namespace_id = int(parts["namespace"])
    if namespace_id == 0:
        return key_dbkey
    namespace_name = _namespace_segment(parts.get("namespace_name", ""), namespace_id)
    return f"{namespace_name}__{key_dbkey}"


def _local_for_title_parts(parts: dict) -> str:
    dbkey = _encode_dbkey_segment(parts["dbkey"])
    namespace_id = int(parts["namespace"])
    if namespace_id == 0:
        return dbkey + ".mw"
    namespace_dir = _namespace_local_dir(parts.get("namespace_name", ""), namespace_id)
    return os.path.join(namespace_dir, dbkey + ".mw")


def _article_fields_from_title(config: dict, raw_title: str, *,
                               fetch_namespaces: bool = True) -> tuple[str, dict]:
    if ":" in raw_title:
        namespace_map = _load_namespace_map(config, fetch=fetch_namespaces)
        parts = _parse_title_parts(raw_title, namespace_map)
    else:
        parts = _main_title_parts(raw_title)
    key = _key_for_title_parts(parts)
    fields = {
        "title": parts["title"],
        "url": _article_url_from_title(config, parts["title"]),
        "local": _local_for_title_parts(parts),
    }
    if int(parts["namespace"]) != 0:
        fields["namespace"] = int(parts["namespace"])
        fields["namespace_name"] = parts["namespace_name"]
        fields["dbkey"] = parts["dbkey"]
    return key, fields


def _parse_article_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        print(f"Error: invalid URL: {url}", file=sys.stderr)
        sys.exit(1)
    if "/wiki/" not in parsed.path:
        print(f"Error: URL does not look like a /wiki/ page: {url}", file=sys.stderr)
        sys.exit(1)

    title_encoded = parsed.path.split("/wiki/", 1)[1]
    return urllib.parse.unquote(title_encoded).replace("_", " ")


def _parse_article_name(name: str) -> str:
    raw = name.strip()
    if not raw:
        print("Error: article name cannot be empty.", file=sys.stderr)
        sys.exit(1)
    if raw.endswith(".mw"):
        raw = raw[:-3]
    return raw.replace("_", " ")


def _article_url_from_title(config: dict, title: str) -> str:
    parsed = urllib.parse.urlparse(get_api_base(config))
    if not parsed.scheme or not parsed.netloc:
        return ""
    title_path = title.replace(" ", "_")

    path = parsed.path
    if path.endswith("/w/api.php"):
        article_path = path[:-len("/w/api.php")] + "/wiki/" + urllib.parse.quote(title_path, safe="/:")
    elif path.endswith("/api.php"):
        article_path = path[:-len("/api.php")] + "/wiki/" + urllib.parse.quote(title_path, safe="/:")
    else:
        article_path = "/wiki/" + urllib.parse.quote(title_path, safe="/:")
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, article_path, "", "", ""))


def _article_url_from_key(config: dict, key: str) -> str:
    return _article_url_from_title(config, key.replace("_", " "))


def _article_url_prefix(config: dict) -> tuple[str, str, str] | None:
    parsed = urllib.parse.urlparse(get_api_base(config))
    if not parsed.scheme or not parsed.netloc:
        return None
    path = parsed.path
    if path.endswith("/w/api.php"):
        prefix = path[:-len("/w/api.php")] + "/wiki/"
    elif path.endswith("/api.php"):
        prefix = path[:-len("/api.php")] + "/wiki/"
    else:
        prefix = "/wiki/"
    return parsed.scheme, parsed.netloc, prefix


def _validate_article_url_matches_wiki(config: dict, url: str) -> None:
    expected = _article_url_prefix(config)
    if expected is None:
        return
    expected_scheme, expected_netloc, expected_prefix = expected
    parsed = urllib.parse.urlparse(url)
    if (parsed.scheme != expected_scheme
            or parsed.netloc != expected_netloc
            or not parsed.path.startswith(expected_prefix)):
        expected_example = urllib.parse.urlunparse(
            (expected_scheme, expected_netloc, expected_prefix + "Page_Title", "", "", "")
        )
        print(f"Error: URL is not from the configured wiki: {url}", file=sys.stderr)
        print(f"Configured wiki expects URLs like: {expected_example}", file=sys.stderr)
        sys.exit(1)


def _article_url(config: dict, key: str, art: dict) -> str:
    return art.get("url") or _article_url_from_title(config, art.get("title", key))


def _looks_like_article_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return bool(parsed.scheme and parsed.netloc and "/wiki/" in parsed.path)


def find_article_entry(config: dict, key: str, *, allow_legacy: bool = False) -> tuple[str, dict] | None:
    wiki = config.setdefault("wiki", {})
    articles = wiki.setdefault("articles", {})
    if key in articles:
        if _legacy_non_main_entry(config, key, articles[key]) and not allow_legacy:
            _fail_legacy_non_main(key, articles[key])
        return key, articles[key]

    target = key[:-3] if key.endswith(".mw") else key
    local_matches = [
        (article_key, art)
        for article_key, art in articles.items()
        if (art.get("local", article_key + ".mw") == key
            or (art.get("local", article_key + ".mw").endswith(".mw")
                and art.get("local", article_key + ".mw")[:-3] == target))
    ]
    if len(local_matches) == 1:
        article_key, art = local_matches[0]
        if _legacy_non_main_entry(config, article_key, art) and not allow_legacy:
            _fail_legacy_non_main(article_key, art)
        return local_matches[0]
    if len(local_matches) > 1:
        print(
            f"Error: local filename '{key}' matches multiple articles in mwsync.yaml.",
            file=sys.stderr,
        )
        sys.exit(1)

    if _looks_like_article_url(key):
        _validate_article_url_matches_wiki(config, key)
        raw_title = _parse_article_url(key)
    else:
        raw_title = _parse_article_name(key)

    if ":" in raw_title:
        requested_title = _canonical_title(raw_title)
        legacy_matches = [
            (article_key, art)
            for article_key, art in articles.items()
            if _legacy_non_main_entry(config, article_key, art)
            and _canonical_title(art.get("title", article_key)) == requested_title
        ]
        if legacy_matches and not allow_legacy:
            print(f"Error: article reference '{key}' matches legacy non-main entry:",
                  file=sys.stderr)
            print(f"Matches: {', '.join(match[0] for match in legacy_matches)}",
                  file=sys.stderr)
            print("Run 'mwsync.py migrate' to update namespace metadata and local paths.",
                  file=sys.stderr)
            sys.exit(1)
        namespace_map = _load_namespace_map(config, fetch=True,
                                            allow_fallback=allow_legacy)
        target_parts = _parse_title_parts(raw_title, namespace_map)
    else:
        namespace_map = None
        target_parts = _main_title_parts(raw_title)
    target_ns = int(target_parts["namespace"])
    target_dbkey = target_parts["dbkey"]
    title_matches = []
    legacy_matches = []
    for article_key, art in articles.items():
        if _legacy_non_main_entry(config, article_key, art):
            if allow_legacy and _canonical_title(art.get("title", article_key)) == target_parts["title"]:
                title_matches.append((article_key, art))
            elif target_ns != 0 and _canonical_title(art.get("title", article_key)) == target_parts["title"]:
                legacy_matches.append((article_key, art))
            continue
        art_parts = _article_parts(config, article_key, art, namespace_map)
        if int(art_parts["namespace"]) == target_ns and art_parts["dbkey"] == target_dbkey:
            title_matches.append((article_key, art))

    if len(title_matches) == 1:
        return title_matches[0]
    if len(title_matches) > 1:
        print(f"Error: article reference '{key}' matches multiple articles.", file=sys.stderr)
        print(f"Matches: {', '.join(match[0] for match in title_matches)}", file=sys.stderr)
        sys.exit(1)
    if legacy_matches and not allow_legacy:
        print(f"Error: article reference '{key}' matches legacy non-main entry:",
              file=sys.stderr)
        print(f"Matches: {', '.join(match[0] for match in legacy_matches)}", file=sys.stderr)
        print("Run 'mwsync.py migrate' to update namespace metadata and local paths.",
              file=sys.stderr)
        sys.exit(1)
    return None


def _canonical_title(title: str) -> str:
    if ":" in title:
        prefix, rest = title.split(":", 1)
        return prefix.replace("_", " ") + ":" + _title_from_dbkey(_normalize_dbkey(rest))
    return _title_from_dbkey(_normalize_dbkey(title))


def _article_parts(config: dict, key: str, art: dict,
                   namespace_map: dict | None = None, *,
                   allow_legacy: bool = False) -> dict:
    if "namespace" in art and "dbkey" in art:
        namespace_id = int(art.get("namespace") or 0)
        if namespace_id != 0 and not art.get("namespace_name") and not allow_legacy:
            _fail_legacy_non_main(key, art)
        if namespace_map is None and namespace_id != 0 and not art.get("namespace_name"):
            namespace_map = _load_namespace_map(config, fetch=False,
                                                allow_fallback=allow_legacy)
        namespace_name = art.get("namespace_name") or (
            "" if namespace_id == 0 else _namespace_name(namespace_map or {}, namespace_id)
        )
        dbkey = _normalize_dbkey(str(art.get("dbkey") or ""))
        return {
            "title": art.get("title", key),
            "namespace": namespace_id,
            "namespace_name": namespace_name,
            "dbkey": dbkey,
        }
    raw_title = art.get("title", key)
    if ":" not in raw_title:
        return _main_title_parts(raw_title)
    if ":" in raw_title and not allow_legacy:
        _fail_legacy_non_main(key, art)
    if namespace_map is None:
        namespace_map = _load_namespace_map(config, fetch=False,
                                            allow_fallback=allow_legacy)
    return _parse_title_parts(raw_title, namespace_map)


def _register_article_target(config: dict, config_path: str, target: str,
                             allow_existing: bool = False,
                             save: bool = True) -> tuple[str, dict, bool]:
    if _looks_like_article_url(target):
        _validate_article_url_matches_wiki(config, target)
        raw_title = _parse_article_url(target)
    else:
        raw_title = _parse_article_name(target)

    existing = find_article_entry(config, target)
    if existing:
        existing_key, existing_art = existing
        if allow_existing:
            return existing_key, existing_art, False
        print(f"Error: article '{existing_key}' is already registered in {config_path}.",
              file=sys.stderr)
        sys.exit(1)

    key, fields = _article_fields_from_title(config, raw_title, fetch_namespaces=":" in raw_title)

    wiki = config.setdefault("wiki", {})
    articles = wiki.setdefault("articles", {})

    if key in articles:
        if allow_existing:
            return key, articles[key], False
        print(f"Error: article '{key}' is already registered in {config_path}.", file=sys.stderr)
        sys.exit(1)

    articles[key] = fields
    if save and not save_config(config, config_path):
        sys.exit(1)
    return key, articles[key], True


def resolve_article_entry(config: dict, key: str) -> tuple[str, dict]:
    """Look up article entry by key or local filename; return canonical key and entry."""
    found = find_article_entry(config, key)
    if found:
        return found

    articles = config.get("wiki", {}).get("articles", {})
    known = list(articles.keys())
    print(f"Error: article '{key}' not found in mwsync.yaml.", file=sys.stderr)
    if known:
        print(f"Known articles: {', '.join(known)}", file=sys.stderr)
    else:
        print("No articles registered yet. Use 'mwsync.py add URL' to add one.",
              file=sys.stderr)
    sys.exit(1)


def resolve_article_entry_for_migration(config: dict, key: str) -> tuple[str, dict]:
    found = find_article_entry(config, key, allow_legacy=True)
    if found:
        return found
    articles = config.get("wiki", {}).get("articles", {})
    known = list(articles.keys())
    print(f"Error: article '{key}' not found in mwsync.yaml.", file=sys.stderr)
    if known:
        print(f"Known articles: {', '.join(known)}", file=sys.stderr)
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
        f"\n# Edit summary for pending wiki commit.\n"
        f"# Lines starting with '#' are stripped.\n"
        f"# An empty summary aborts the commit.\n"
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


def _pending_commit_meta_path(key: str) -> str:
    return os.path.join(_cache_dir(key), "commit.json")


def _pending_commit_body_path(key: str) -> str:
    return os.path.join(_cache_dir(key), "commit.mw")


def _merge_state_path(key: str) -> str:
    return os.path.join(_cache_dir(key), "merge.json")


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


def _default_local_for_article(config: dict, key: str, art: dict,
                               namespace_map: dict | None = None,
                               *, allow_legacy: bool = False) -> str:
    parts = _article_parts(config, key, art, namespace_map, allow_legacy=allow_legacy)
    return _local_for_title_parts(parts)


def _article_has_non_main_title(config: dict, key: str, art: dict,
                                namespace_map: dict | None = None,
                                *, allow_legacy: bool = False) -> bool:
    parts = _article_parts(config, key, art, namespace_map, allow_legacy=allow_legacy)
    return int(parts.get("namespace") or 0) != 0


def _new_key_for_article(config: dict, key: str, art: dict,
                         namespace_map: dict | None = None,
                         *, allow_legacy: bool = False) -> str:
    parts = _article_parts(config, key, art, namespace_map, allow_legacy=allow_legacy)
    return _key_for_title_parts(parts)


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


def _read_optional_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _read_json_file(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        print(f"Error: expected JSON object in {path}", file=sys.stderr)
        sys.exit(1)
    return data


def _restore_optional_text(path: str, content: str | None) -> None:
    if content is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    _atomic_write(path, content)


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


def _history_content(entries: list[dict]) -> str:
    seen = {}
    for entry in entries:
        revid = entry.get("revid")
        if revid is not None:
            rid = int(revid)
            seen[rid] = {**seen.get(rid, {}), **entry}
    ordered = sorted(seen.values(), key=lambda e: (e.get("timestamp", ""), int(e["revid"])))
    return "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n"
                   for e in ordered)


def _write_history(key: str, entries: list[dict]) -> bool:
    return _atomic_write(_history_path(key), _history_content(entries))


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


def _cache_fetch_transaction(key: str, art: dict, api_base: str,
                             latest_result: dict,
                             metadata_revs: list[dict],
                             body_results: list[dict]) -> bool:
    cache_dir = _cache_dir(key)
    cache_existed = os.path.exists(cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".fetch-", dir=cache_dir)
    promoted_paths: list[str] = []
    old_history = _read_optional_text(_history_path(key))

    staged_files: list[tuple[str, str]] = []
    history_entries: list[dict] = []

    def stage_text(name: str, content: str) -> None:
        path = os.path.join(staging, name)
        if not _atomic_write(path, content):
            raise RuntimeError(f"failed to stage {name}")
        staged_files.append((path, os.path.join(cache_dir, name)))

    def stage_json(name: str, data: dict) -> None:
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        stage_text(name, text)

    def add_record(result: dict) -> None:
        record = _revision_record(key, art, result, api_base)
        revid = int(record["revid"])
        meta_name = f"{revid}.json"
        body_name = f"{revid}.mw"
        meta_path = _revision_meta_path(key, revid)
        body_path = _revision_body_path(key, revid)

        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    existing_meta = json.load(f)
            except Exception as e:
                raise RuntimeError(f"could not read {meta_path}: {e}") from e
            if (existing_meta.get("sha1") and record.get("sha1")
                    and existing_meta.get("sha1") != record.get("sha1")):
                raise RuntimeError(f"cached revision {revid} metadata differs")
        else:
            stage_json(meta_name, record)

        if "wikitext" in result:
            if os.path.exists(body_path):
                pass
            else:
                stage_text(body_name, result["wikitext"])

        history_entries.append(_history_entry(record))

    try:
        add_record(latest_result)
        for rev in metadata_revs:
            add_record(rev)
        for result in body_results:
            add_record(result)

        latest_revid = int(latest_result["revid"])
        staged_targets = {target for _source, target in staged_files}
        if (_revision_body_path(key, latest_revid) not in staged_targets
                and not os.path.exists(_revision_body_path(key, latest_revid))):
            raise RuntimeError(f"latest body missing for revid {latest_revid}")
        if (_revision_meta_path(key, latest_revid) not in staged_targets
                and not os.path.exists(_revision_meta_path(key, latest_revid))):
            raise RuntimeError(f"latest metadata missing for revid {latest_revid}")

        new_history = _read_history(key) + history_entries
        history_text = _history_content(new_history)

        for source, target in staged_files:
            if os.path.exists(target):
                continue
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            os.replace(source, target)
            promoted_paths.append(target)

        if not _atomic_write(_history_path(key), history_text):
            raise RuntimeError(f"failed to write {_history_path(key)}")
        if not _write_ref(key, "upstream", latest_revid):
            raise RuntimeError(f"failed to write {_ref_path(key, 'upstream')}")
        return True
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        _restore_optional_text(_history_path(key), old_history)
        for path in reversed(promoted_paths):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        if not cache_existed:
            try:
                shutil.rmtree(cache_dir)
            except OSError:
                pass
            try:
                os.rmdir("_cache")
            except OSError:
                pass
        return False
    finally:
        try:
            shutil.rmtree(staging)
        except OSError:
            pass


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
        found = find_article_entry(config, spec)
        if found:
            key, art = found
            local = art.get("local", key + ".mw")
            return local, f"{local} (local)"
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


def _update_upstream_config(config: dict, key: str, result: dict) -> None:
    wiki = config.setdefault("wiki", {})
    articles = wiki.setdefault("articles", {})
    art = articles.setdefault(key, {})
    art["upstream_revid"] = result["revid"]
    art["upstream_timestamp"] = result["timestamp"]
    art["upstream_editor"] = result["user"]
    art["upstream_summary"] = result["comment"]
    art["upstream_sha1"] = result["sha1"]


def _update_upstream_config_from_cache(config: dict, key: str, revid: int) -> bool:
    meta_path = _revision_meta_path(key, revid)
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        print(f"Error reading cached metadata {meta_path}: {e}", file=sys.stderr)
        return False
    _update_upstream_config(config, key, {
        "revid": int(meta.get("revid") or revid),
        "timestamp": meta.get("timestamp", ""),
        "user": meta.get("user", ""),
        "comment": meta.get("comment", ""),
        "sha1": meta.get("sha1", ""),
    })
    return True


def _write_base_and_upstream_config(config: dict, config_path: str, key: str,
                                    revid: int) -> bool:
    if not _write_ref(key, "base", revid):
        return False
    if not _update_upstream_config_from_cache(config, key, revid):
        return False
    return save_config(config, config_path)


def _pending_commit(key: str) -> dict | None:
    meta = _read_json_file(_pending_commit_meta_path(key))
    if meta is None:
        return None
    body_path = _pending_commit_body_path(key)
    if not os.path.exists(body_path):
        print(f"Error: pending commit metadata exists but body is missing: {body_path}",
              file=sys.stderr)
        sys.exit(1)
    return meta


def _clear_pending_commit(key: str) -> None:
    for path in (_pending_commit_meta_path(key), _pending_commit_body_path(key)):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _write_pending_commit(key: str, meta: dict, text: str) -> bool:
    if not _atomic_write(_pending_commit_body_path(key), text):
        return False
    return _write_json(_pending_commit_meta_path(key), meta)


def _read_merge_state(key: str) -> dict | None:
    return _read_json_file(_merge_state_path(key))


def _write_merge_state(key: str, state: dict) -> bool:
    return _write_json(_merge_state_path(key), state)


def _clear_merge_state(key: str) -> None:
    try:
        os.unlink(_merge_state_path(key))
    except FileNotFoundError:
        pass


def _has_conflict_markers(text: str) -> bool:
    return any(line.startswith(("<<<<<<< ", ">>>>>>> ")) or line == "======="
               for line in text.splitlines())


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
    key, art, _created = _register_article_target(config, config_path, args.article)
    url = _article_url(config, key, art)
    print(f"Registered '{key}'", file=sys.stderr)
    print(f"  title: {art.get('title', key)}", file=sys.stderr)
    if url:
        print(f"  url:   {url}", file=sys.stderr)
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

    found = None if _looks_like_article_url(target) else find_article_entry(config, target)
    if found:
        key, art = found
        created = False
    else:
        cache_existed = None
        key, art, created = _register_article_target(
            config, config_path, target, allow_existing=True, save=False,
        )
        if created:
            local = art.get("local", key + ".mw")
            if os.path.exists(local):
                print(f"Error: local file already exists: {local}", file=sys.stderr)
                print("Move it aside or register the article explicitly before checkout.",
                      file=sys.stderr)
                sys.exit(1)
            cache_existed = os.path.exists(_cache_dir(key))

    if created:
        fetch_args = argparse.Namespace(article=key, dry_run=False, depth=depth, quiet=True)
        fetch_info = run_fetch(fetch_args, config, config_path)
        upstream_revid = int((fetch_info or {}).get("revid") or 0)
        local = art.get("local", key + ".mw")
        try:
            upstream_text = _read_text(_cached_body_or_die(key, upstream_revid))
            if not _write_ref(key, "base", upstream_revid):
                raise RuntimeError(f"failed to write {_ref_path(key, 'base')}")
            if not _atomic_write(local, upstream_text):
                raise RuntimeError(f"failed to write {local}")
            if not _update_upstream_config_from_cache(config, key, upstream_revid):
                raise RuntimeError(f"failed to update upstream metadata for {key}")
            if not save_config(config, config_path):
                raise RuntimeError(f"failed to save {config_path}")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            try:
                os.unlink(local)
            except FileNotFoundError:
                pass
            if cache_existed is False:
                try:
                    shutil.rmtree(_cache_dir(key))
                except OSError:
                    pass
                try:
                    os.rmdir("_cache")
                except OSError:
                    pass
            sys.exit(1)

        title = art.get("title", key)
        url = _article_url(config, key, art)
        print(f"# Registered '{key}'", file=sys.stderr)
        print(f"# Checked out {local} from '{title}' at revid {upstream_revid}",
              file=sys.stderr)
        if url:
            print(f"# URL: {url}", file=sys.stderr)
        return

    fetch_args = argparse.Namespace(article=key, dry_run=False, depth=depth, quiet=True)
    fetch_info = run_fetch(fetch_args, config, config_path)
    merge_args = argparse.Namespace(article=key, quiet=True)
    merge_info = run_merge(merge_args, config, config_path)

    key, art = resolve_article_entry(config, key)
    local = art.get("local", key + ".mw")
    title = art.get("title", key)
    url = _article_url(config, key, art)
    revid = (merge_info or {}).get("upstream_revid") or (fetch_info or {}).get("revid")
    action = (merge_info or {}).get("action")
    if action == "checked-out":
        print(f"# Checked out {local} from '{title}' at revid {revid}", file=sys.stderr)
    elif action == "adopted":
        print(f"# Adopted existing {local} as checkout of revid {revid}", file=sys.stderr)
    elif action == "already-up-to-date":
        print(f"# {local} already up to date at revid {revid}", file=sys.stderr)
    elif action == "local-matches-upstream":
        print(f"# {local} already matched upstream revid {revid}", file=sys.stderr)
    elif action == "fast-forwarded":
        old_revid = (merge_info or {}).get("base_revid")
        print(f"# Updated {local} from revid {old_revid} to {revid}", file=sys.stderr)
    elif action == "merged":
        print(f"# Merged upstream revid {revid} into {local}", file=sys.stderr)
    else:
        print(f"# Checkout complete for {local} at revid {revid}", file=sys.stderr)
    if url:
        print(f"# URL: {url}", file=sys.stderr)


def run_fetch(args, config: dict, config_path: str) -> dict | None:
    key, art = resolve_article_entry(config, args.article)
    _check_legacy_cache(key)
    title = art.get("title", key)
    local = art.get("local", key + ".mw")
    url = _article_url(config, key, art)
    api_base = get_api_base(config)
    dry_run = getattr(args, "dry_run", False)
    all_known = getattr(args, "all_known", False)
    with_bodies = getattr(args, "with_bodies", False)
    quiet = getattr(args, "quiet", False)
    depth_arg = getattr(args, "depth", DEFAULT_HISTORY_DEPTH)
    depth = None if all_known else max(1, int(depth_arg or 1))

    if dry_run:
        depth_label = "all available" if all_known else str(depth)
        print(f"# Fetch plan for: {key}", file=sys.stderr)
        print(f"#   Title:    {title}", file=sys.stderr)
        if url:
            print(f"#   URL:      {url}", file=sys.stderr)
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

    if not quiet:
        print(f"# Fetching '{title}' from {api_base}...", file=sys.stderr)
        if url:
            print(f"# URL: {url}", file=sys.stderr)
    try:
        result = _fetch_page(title, api_base)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    revid = result["revid"]
    wikitext = result["wikitext"]
    if not quiet:
        print(f"# Got revid {revid} ({len(wikitext)} chars)", file=sys.stderr)

    metadata_revs: list[dict] = []
    body_results: list[dict] = []
    if all_known or depth > 1:
        if all_known:
            if not quiet:
                print("# Fetching metadata for all available revisions...", file=sys.stderr)
        else:
            if not quiet:
                print(f"# Fetching metadata for newest {depth} revisions...", file=sys.stderr)
        try:
            metadata_revs = _fetch_revision_metadata(title, api_base, depth)
            for rev in metadata_revs:
                if with_bodies and int(rev.get("revid") or 0) != int(revid):
                    old_revid = int(rev["revid"])
                    if not os.path.exists(_revision_body_path(key, old_revid)):
                        if not quiet:
                            print(f"# Fetching body for revid {old_revid}...",
                                  file=sys.stderr)
                        body_results.append(_fetch_revision_by_revid(old_revid, api_base))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    if not _cache_fetch_transaction(key, art, api_base, result,
                                    metadata_revs, body_results):
        sys.exit(1)
    if not quiet:
        print(f"# Cached revision {_revision_body_path(key, revid)}", file=sys.stderr)
        print(f"# Updated refs/upstream to {revid}", file=sys.stderr)
        print(f"# Left {local} unchanged; run 'mwsync.py merge {key}' to update it.",
              file=sys.stderr)
    return {
        "key": key,
        "title": title,
        "local": local,
        "url": url,
        "revid": int(revid),
        "depth": depth,
        "all_known": all_known,
        "with_bodies": with_bodies,
    }


def run_commit(args, config: dict, config_path: str) -> None:
    key, art = resolve_article_entry(config, args.article)
    _check_legacy_cache(key)
    title = art.get("title", key)
    local = art.get("local", key + ".mw")
    url = _article_url(config, key, art)
    baserevid = _read_ref(key, "base") or art.get("upstream_revid", 0)
    message = getattr(args, "message", None)
    create_new = getattr(args, "new", False)
    amend = getattr(args, "amend", False)
    allow_empty = getattr(args, "allow_empty", False)
    pending = _pending_commit(key)

    if not baserevid and not create_new:
        print(f"Error: upstream_revid not set for '{key}'.", file=sys.stderr)
        print(f"If this is a new article, use 'mwsync.py commit --new {key}' first.",
              file=sys.stderr)
        print(f"Otherwise, run 'mwsync.py fetch {key}' first.", file=sys.stderr)
        sys.exit(1)

    if pending and not amend:
        print(f"Error: pending commit already exists for '{key}'.", file=sys.stderr)
        print(f"Run 'mwsync.py push {key}' to publish it, or "
              f"'mwsync.py commit --amend {key}' to replace it.",
              file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(local):
        print(f"Error: local file not found: {local}", file=sys.stderr)
        print(f"Run 'mwsync.py merge {key}' first, or create {local}.", file=sys.stderr)
        sys.exit(1)

    try:
        page_text = _read_text(local)
    except Exception as e:
        print(f"Error reading {local}: {e}", file=sys.stderr)
        sys.exit(1)

    merge_state = None if create_new else _read_merge_state(key)
    if merge_state:
        if _has_conflict_markers(page_text):
            print(f"Error: {local} still contains merge conflict markers.",
                  file=sys.stderr)
            print("Resolve the conflicts before running mwsync.py commit.",
                  file=sys.stderr)
            sys.exit(1)
        try:
            merge_upstream = int(merge_state.get("upstream_revid") or 0)
        except (TypeError, ValueError):
            merge_upstream = 0
        if merge_upstream:
            baserevid = merge_upstream

    if not create_new and not allow_empty and baserevid:
        base_path = _cached_body_or_die(key, int(baserevid))
        if _read_text(base_path) == page_text:
            print(f"Nothing to commit for '{key}'.", file=sys.stderr)
            print("Use --allow-empty to create a pending commit anyway.",
                  file=sys.stderr)
            sys.exit(1)

    if message:
        summary = message
    elif amend and pending and pending.get("summary"):
        summary = _edit_summary(str(pending.get("summary", "")), key, title, int(baserevid))
    else:
        summary = _edit_summary("", key, title, int(baserevid or 0))
    if summary is None:
        print("# Aborted: empty edit summary.", file=sys.stderr)
        sys.exit(0)

    created_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "article_key": key,
        "title": title,
        "url": url,
        "local": local,
        "base_revid": int(baserevid or 0),
        "create_new": bool(create_new),
        "summary": summary,
        "created_at": created_at,
        "body": os.path.basename(_pending_commit_body_path(key)),
    }
    if not _write_pending_commit(key, meta, page_text):
        sys.exit(1)
    if merge_state:
        _clear_merge_state(key)

    action = "Amended" if amend and pending else "Committed"
    print(f"# {action} pending edit for '{key}'", file=sys.stderr)
    print(f"#   Title:      {title}", file=sys.stderr)
    if url:
        print(f"#   URL:        {url}", file=sys.stderr)
    print(f"#   Local:      {local} ({len(page_text)} chars)", file=sys.stderr)
    if create_new:
        print("#   Mode:       CREATE NEW article", file=sys.stderr)
    else:
        print(f"#   Base revid: {baserevid}", file=sys.stderr)
    print(f"#   Summary:    {summary}", file=sys.stderr)
    print(f"# Run: mwsync.py push {key}", file=sys.stderr)


def run_push(args, config: dict, config_path: str) -> None:
    key, art = resolve_article_entry(config, args.article)
    _check_legacy_cache(key)
    title = art.get("title", key)
    local = art.get("local", key + ".mw")
    url = _article_url(config, key, art)
    api_base = get_api_base(config)
    dry_run = getattr(args, "dry_run", False)
    pending = _pending_commit(key)

    if pending is None:
        print(f"Everything up-to-date for '{key}' (no pending commit).", file=sys.stderr)
        print(f"Run 'mwsync.py commit {key} -m \"Summary\"' first.", file=sys.stderr)
        return

    commit_title = str(pending.get("title") or title)
    baserevid = int(pending.get("base_revid") or 0)
    create_new = bool(pending.get("create_new", False))
    summary = str(pending.get("summary") or "").strip()
    commit_local = str(pending.get("local") or local)
    commit_url = str(pending.get("url") or url)
    body_path = _pending_commit_body_path(key)

    if not summary:
        print(f"Error: pending commit for '{key}' has an empty summary.", file=sys.stderr)
        print(f"Repair {_pending_commit_meta_path(key)} or recommit with --amend.",
              file=sys.stderr)
        sys.exit(1)

    if not baserevid and not create_new:
        print(f"Error: pending commit for '{key}' has no base revid.", file=sys.stderr)
        print(f"If this is a new article, recommit with 'mwsync.py commit --new {key}'.",
              file=sys.stderr)
        sys.exit(1)

    try:
        page_text = _read_text(body_path)
    except Exception as e:
        print(f"Error reading {body_path}: {e}", file=sys.stderr)
        sys.exit(1)

    username = os.environ.get("MWSYNC_MW_USER", "")
    password = os.environ.get("MWSYNC_MW_PASSWORD", "")

    if dry_run:
        print(f"# Push plan for: {key}", file=sys.stderr)
        print(f"#   Title:      {commit_title}", file=sys.stderr)
        if commit_url:
            print(f"#   URL:        {commit_url}", file=sys.stderr)
        print(f"#   API:        {api_base}", file=sys.stderr)
        print(f"#   Commit:     {body_path} ({len(page_text)} chars)", file=sys.stderr)
        print(f"#   Local:      {commit_local}", file=sys.stderr)
        if create_new:
            print("#   Mode:       CREATE NEW article", file=sys.stderr)
        else:
            print(f"#   Base revid: {baserevid}", file=sys.stderr)
        print(f"#   Summary:    {summary}", file=sys.stderr)
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

    print(f"# Pushing '{key}'...", file=sys.stderr)
    print(f"#   Title:      {commit_title}", file=sys.stderr)
    if commit_url:
        print(f"#   URL:        {commit_url}", file=sys.stderr)
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
        new_revid = _mw_edit_page(api_base, opener, commit_title, page_text,
                                   baserevid, csrf_token, summary,
                                   create_new=create_new)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"# Success! New revid: {new_revid}", file=sys.stderr)
    if commit_url:
        print(f"# URL: {commit_url}", file=sys.stderr)

    now_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wiki = config.setdefault("wiki", {})
    articles = wiki.setdefault("articles", {})
    art = articles.setdefault(key, {})
    art["last_pushed_revid"] = new_revid
    art["last_pushed_at"] = now_utc
    if not _write_ref(key, "last-pushed", int(new_revid)):
        sys.exit(1)
    save_config(config, config_path)
    _clear_pending_commit(key)

    # Auto-fetch to resync upstream refs with the revision we just created.
    print("# Re-fetching to sync upstream cache...", file=sys.stderr)
    try:
        result = _fetch_page(commit_title, api_base)
        if not _cache_fetch_transaction(key, art, api_base, result, [], []):
            sys.exit(1)
        if not _write_ref(key, "base", int(result["revid"])):
            sys.exit(1)
        _atomic_write(local, result["wikitext"])
        _update_upstream_config(config, key, result)
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
            if not _cache_fetch_transaction(key, art, api_base, result, [], []):
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


def run_merge(args, config: dict, config_path: str) -> dict | None:
    key, art = resolve_article_entry(config, args.article)
    _check_legacy_cache(key)
    local = art.get("local", key + ".mw")
    api_base = get_api_base(config)
    quiet = getattr(args, "quiet", False)

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
        if not _write_base_and_upstream_config(config, config_path, key, upstream_revid):
            sys.exit(1)
        if not quiet:
            print(f"# Checked out {local} at upstream revid {upstream_revid}",
                  file=sys.stderr)
            print(f"# Updated refs/base to {upstream_revid}", file=sys.stderr)
            print(f"# Updated upstream_revid={upstream_revid} in {config_path}",
                  file=sys.stderr)
        _clear_merge_state(key)
        return {"action": "checked-out", "upstream_revid": upstream_revid}

    if base_revid is None:
        if _file_content_matches(local, upstream_text):
            if not _write_base_and_upstream_config(config, config_path, key, upstream_revid):
                sys.exit(1)
            if not quiet:
                print(f"# Adopted existing {local} as refs/base {upstream_revid}",
                      file=sys.stderr)
                print(f"# Updated upstream_revid={upstream_revid} in {config_path}",
                      file=sys.stderr)
            _clear_merge_state(key)
            return {"action": "adopted", "upstream_revid": upstream_revid}
        print(f"Error: no base revision cached for '{key}'.", file=sys.stderr)
        print(f"Run 'mwsync.py fetch {key}' before making local edits.", file=sys.stderr)
        sys.exit(1)

    base_path = _cached_body_or_die(key, base_revid)
    base_text = _read_text(base_path)

    if int(base_revid) == int(upstream_revid):
        if not quiet:
            print(f"# Already up to date at revid {upstream_revid}", file=sys.stderr)
        _clear_merge_state(key)
        return {
            "action": "already-up-to-date",
            "base_revid": base_revid,
            "upstream_revid": upstream_revid,
        }

    if _file_content_matches(local, upstream_text):
        if not _write_base_and_upstream_config(config, config_path, key, upstream_revid):
            sys.exit(1)
        if not quiet:
            print(f"# Local file already matches upstream revid {upstream_revid}",
                  file=sys.stderr)
            print(f"# Updated refs/base to {upstream_revid}", file=sys.stderr)
            print(f"# Updated upstream_revid={upstream_revid} in {config_path}",
                  file=sys.stderr)
        _clear_merge_state(key)
        return {
            "action": "local-matches-upstream",
            "base_revid": base_revid,
            "upstream_revid": upstream_revid,
        }

    if _file_content_matches(local, base_text):
        if not _atomic_write(local, upstream_text):
            sys.exit(1)
        if not _write_base_and_upstream_config(config, config_path, key, upstream_revid):
            sys.exit(1)
        if not quiet:
            print(f"# Fast-forwarded {local} from {base_revid} to {upstream_revid}",
                  file=sys.stderr)
            print(f"# Updated upstream_revid={upstream_revid} in {config_path}",
                  file=sys.stderr)
        _clear_merge_state(key)
        return {
            "action": "fast-forwarded",
            "base_revid": base_revid,
            "upstream_revid": upstream_revid,
        }

    code, merged_text, merge_stderr = _run_merge_file(local, base_path, upstream_path)
    if code == 0:
        if not _atomic_write(local, merged_text):
            sys.exit(1)
        if not _write_base_and_upstream_config(config, config_path, key, upstream_revid):
            sys.exit(1)
        if not quiet:
            print(f"# Merged upstream revid {upstream_revid} into {local}",
                  file=sys.stderr)
            print(f"# Updated refs/base to {upstream_revid}", file=sys.stderr)
            print(f"# Updated upstream_revid={upstream_revid} in {config_path}",
                  file=sys.stderr)
        _clear_merge_state(key)
        return {
            "action": "merged",
            "base_revid": base_revid,
            "upstream_revid": upstream_revid,
        }

    if code == 1:
        if not _atomic_write(local, merged_text):
            sys.exit(1)
        state = {
            "article_key": key,
            "base_revid": int(base_revid),
            "upstream_revid": int(upstream_revid),
            "created_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if not _write_merge_state(key, state):
            sys.exit(1)
        print(f"Conflict: merged with conflict markers in {local}", file=sys.stderr)
        print("Resolve conflicts, then run 'mwsync.py commit'.", file=sys.stderr)
        print(f"Merge state saved in {_merge_state_path(key)}.", file=sys.stderr)
        sys.exit(1)

    if merge_stderr:
        print(merge_stderr.rstrip(), file=sys.stderr)
    print("Error: git merge-file failed.", file=sys.stderr)
    sys.exit(1)


def run_restore(args, config: dict, config_path: str) -> None:
    key, art = resolve_article_entry(config, args.article)
    _check_legacy_cache(key)
    local = art.get("local", key + ".mw")
    discard_commit = getattr(args, "discard_commit", False)
    abort_merge = getattr(args, "abort_merge", False)
    dry_run = getattr(args, "dry_run", False)

    base_revid = _read_ref(key, "base")
    if base_revid is None:
        print(f"Error: no base revision cached for '{key}'.", file=sys.stderr)
        print(f"Run 'mwsync.py fetch {key}' and 'mwsync.py merge {key}' first.",
              file=sys.stderr)
        sys.exit(1)

    merge_state_exists = os.path.exists(_merge_state_path(key))
    merge_state = None if abort_merge else _read_merge_state(key)
    if merge_state and not abort_merge:
        print(f"Error: merge state exists for '{key}'.", file=sys.stderr)
        print(f"Use 'mwsync.py restore --abort-merge {key}' to discard the merge.",
              file=sys.stderr)
        sys.exit(1)

    base_path = _cached_body_or_die(key, int(base_revid))
    pending_exists = (os.path.exists(_pending_commit_meta_path(key))
                      or os.path.exists(_pending_commit_body_path(key)))
    pending = None if discard_commit else _pending_commit(key)

    if dry_run:
        print(f"# Restore plan for: {key}", file=sys.stderr)
        print(f"#   Local:       {local}", file=sys.stderr)
        print(f"#   Source:      refs/base ({base_revid})", file=sys.stderr)
        if merge_state_exists:
            print("#   Merge state: will be cleared", file=sys.stderr)
        if pending_exists:
            action = "will be discarded" if discard_commit else "will remain"
            print(f"#   Pending:     {action}", file=sys.stderr)
        return

    base_text = _read_text(base_path)
    if not _atomic_write(local, base_text):
        sys.exit(1)
    if abort_merge:
        _clear_merge_state(key)
    if discard_commit:
        _clear_pending_commit(key)

    print(f"# Restored {local} from refs/base ({base_revid})", file=sys.stderr)
    if abort_merge and merge_state_exists:
        print(f"# Cleared merge state for '{key}'", file=sys.stderr)
    if discard_commit and pending_exists:
        print(f"# Discarded pending commit for '{key}'", file=sys.stderr)
    elif pending:
        print(f"# Pending commit still exists for '{key}'.", file=sys.stderr)
        print(f"# Use 'mwsync.py restore --discard-commit {key}' to discard it.",
              file=sys.stderr)


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


def _fsck_article(config: dict, key: str, art: dict, namespace_map: dict | None = None) -> int:
    if namespace_map is None:
        namespace_map = _load_namespace_map(config, fetch=False, allow_fallback=True)
    issues = 0
    parts = _article_parts(config, key, art, namespace_map, allow_legacy=True)
    namespace_id = int(parts.get("namespace") or 0)
    title = art.get("title", key)

    if namespace_id != 0:
        missing = [field for field in ("namespace", "namespace_name", "dbkey")
                   if field not in art]
        if missing:
            print(f"{key}: missing namespace metadata (title={title})")
            issues += 1

        local = art.get("local", key + ".mw")
        default_local = _local_for_title_parts(parts)
        if os.path.dirname(local) == "":
            print(f"{key}: flat local path for non-main namespace: {local}")
            issues += 1
        elif local != default_local:
            # Non-default local paths are allowed, but highlight likely legacy flat escapes.
            pass

    if ":" in key:
        new_key = _new_key_for_article(config, key, art, namespace_map,
                                       allow_legacy=True)
        print(f"{key}: legacy colon-bearing key, would migrate to {new_key}")
        issues += 1

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

    commit_meta_path = _pending_commit_meta_path(key)
    commit_body_path = _pending_commit_body_path(key)
    if os.path.exists(commit_meta_path) or os.path.exists(commit_body_path):
        if not os.path.exists(commit_meta_path):
            print(f"{key}: pending commit body exists without commit.json")
            issues += 1
        if not os.path.exists(commit_body_path):
            print(f"{key}: pending commit metadata exists without commit.mw")
            issues += 1
        if os.path.exists(commit_meta_path):
            try:
                with open(commit_meta_path, "r", encoding="utf-8") as f:
                    pending = json.load(f)
                if not isinstance(pending, dict):
                    raise ValueError("expected JSON object")
                if pending.get("article_key") and pending["article_key"] != key:
                    print(f"{key}: pending commit article_key mismatch")
                    issues += 1
                if not str(pending.get("summary") or "").strip():
                    print(f"{key}: pending commit has empty summary")
                    issues += 1
                base_revid = int(pending.get("base_revid") or 0)
                if not pending.get("create_new") and not base_revid:
                    print(f"{key}: pending commit has no base_revid")
                    issues += 1
            except Exception as e:
                print(f"{key}: cannot read pending commit metadata {commit_meta_path}: {e}")
                issues += 1

    merge_state_path = _merge_state_path(key)
    if os.path.exists(merge_state_path):
        try:
            with open(merge_state_path, "r", encoding="utf-8") as f:
                merge_state = json.load(f)
            if not isinstance(merge_state, dict):
                raise ValueError("expected JSON object")
            if merge_state.get("article_key") and merge_state["article_key"] != key:
                print(f"{key}: merge state article_key mismatch")
                issues += 1
            for field in ("base_revid", "upstream_revid"):
                revid = int(merge_state.get(field) or 0)
                if not revid:
                    print(f"{key}: merge state missing {field}")
                    issues += 1
                elif history and revid not in seen_revids:
                    print(f"{key}: merge state {field} points outside history: {revid}")
                    issues += 1
        except Exception as e:
            print(f"{key}: cannot read merge state {merge_state_path}: {e}")
            issues += 1

    if issues == 0:
        print(f"{key}: ok")
    return issues


def run_fsck(args, config: dict, config_path: str) -> None:
    articles = config.get("wiki", {}).get("articles", {})
    if not articles:
        print("No articles registered.")
        return

    if getattr(args, "article", None):
        key, art = resolve_article_entry_for_migration(config, args.article)
        items = [(key, art)]
    else:
        items = list(articles.items())

    issues = 0
    namespace_map = _load_namespace_map(config, fetch=False, allow_fallback=True)
    for key, art in items:
        issues += _fsck_article(config, key, art, namespace_map)
    if issues:
        print(f"fsck found {issues} issue(s).", file=sys.stderr)
        sys.exit(1)


def _prompt_yes(prompt: str) -> bool:
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().casefold() in ("y", "yes")


def _migrate_article(config: dict, config_path: str, key: str, art: dict,
                     namespace_map: dict, *, dry_run: bool, yes: bool) -> tuple[str, bool, bool]:
    """Return (current_key, changed, incomplete)."""
    articles = config.setdefault("wiki", {}).setdefault("articles", {})
    parts = _article_parts(config, key, art, namespace_map, allow_legacy=True)
    namespace_id = int(parts.get("namespace") or 0)
    changed = False
    incomplete = False

    if namespace_id != 0:
        missing = [field for field in ("namespace", "namespace_name", "dbkey")
                   if field not in art]
        if missing:
            print(f"{key}: added namespace metadata "
                  f"(namespace={namespace_id}, dbkey={parts['dbkey']})")
            if not dry_run:
                art["namespace"] = namespace_id
                art["namespace_name"] = parts["namespace_name"]
                art["dbkey"] = parts["dbkey"]
                changed = True
            else:
                changed = True

    desired_key = _key_for_title_parts(parts)
    local = art.get("local", key + ".mw")
    desired_local = _local_for_title_parts(parts)
    risky: list[tuple[str, str, str]] = []

    if ":" in key and desired_key != key:
        risky.append(("rename key", key, desired_key))
        if os.path.exists(_cache_dir(key)):
            risky.append(("move cache", _cache_dir(key), _cache_dir(desired_key)))

    if namespace_id != 0 and os.path.dirname(local) == "" and local != desired_local:
        risky.append(("move file", local, desired_local))

    if risky:
        print(f"{key}: ready to migrate this entry:")
        for label, source, target in risky:
            print(f"  {label}:  {source} -> {target}")
        if dry_run:
            return key, True, False
        if not yes and not _prompt_yes("Apply? [y/N] "):
            return key, changed, True

        if ":" in key and desired_key != key and desired_key in articles:
            print(f"Error: cannot rename {key}; article key exists: {desired_key}",
                  file=sys.stderr)
            return key, changed, True
        for label, source, target in risky:
            if label == "rename key" or not os.path.exists(source):
                continue
            if os.path.exists(target):
                print(f"Error: cannot {label}; destination exists: {target}", file=sys.stderr)
                return key, changed, True

        for label, source, target in risky:
            if label == "rename key":
                continue
            if not os.path.exists(source):
                continue
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            try:
                os.replace(source, target)
            except OSError as e:
                print(f"Error: cannot {label} {source} -> {target}: {e}", file=sys.stderr)
                return key, changed, True

        if ":" in key and desired_key != key:
            articles[desired_key] = articles.pop(key)
            key = desired_key
            art = articles[key]
        if namespace_id != 0 and os.path.dirname(local) == "" and local != desired_local:
            art["local"] = desired_local
        changed = True

    if changed and not dry_run:
        if not save_config(config, config_path):
            return key, changed, True
    return key, changed, incomplete


def run_migrate(args, config: dict, config_path: str) -> None:
    articles = config.get("wiki", {}).get("articles", {})
    if not articles:
        print("No articles registered.")
        return

    namespace_map = _load_namespace_map(config, fetch=True, allow_fallback=True)
    dry_run = getattr(args, "dry_run", False)
    yes = getattr(args, "yes", False)

    if getattr(args, "article", None):
        key, art = resolve_article_entry_for_migration(config, args.article)
        items = [(key, art)]
    else:
        items = list(articles.items())

    changed_any = False
    incomplete = False
    for key, art in items:
        current_key, changed, failed = _migrate_article(
            config, config_path, key, art, namespace_map, dry_run=dry_run, yes=yes,
        )
        changed_any = changed_any or changed
        incomplete = incomplete or failed
        if failed:
            print(f"{current_key}: migration incomplete", file=sys.stderr)

    if dry_run:
        if not changed_any:
            print("No migrations needed.")
        return
    if incomplete:
        sys.exit(1)
    if not changed_any:
        print("No migrations needed.")


def run_status(args, config: dict, config_path: str) -> None:
    articles = config.get("wiki", {}).get("articles", {})
    if not articles:
        print("No articles registered. Use 'mwsync.py add URL' to add one.")
        return
    verbose = getattr(args, "verbose", False)

    key_filter = getattr(args, "article", None)
    if key_filter:
        key, art = resolve_article_entry(config, key_filter)
        items = [(key, art)]
    else:
        items = list(articles.items())

    rows = []
    for key, art in items:
        local = art.get("local", key + ".mw")
        url = _article_url(config, key, art)
        upstream_ref = _read_ref(key, "upstream")
        base_ref = _read_ref(key, "base")
        last_pushed_ref = _read_ref(key, "last-pushed")
        pending = _pending_commit(key)
        merge_state = _read_merge_state(key)
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
        if modified is None and os.path.exists(local) and base_ref is not None:
            base_path = _revision_body_path(key, base_ref)
            if os.path.exists(base_path):
                modified = not _file_content_matches(local, _read_text(base_path))
        if modified is True:
            flag = "[modified]"
        elif modified is False:
            flag = "[clean]"
        else:
            flag = ""

        if verbose:
            print(key)
            print(f"  local:           {local}  {flag}".rstrip())
            if url:
                print(f"  url:             {url}")
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
            if pending:
                pending_summary = str(pending.get("summary") or "")
                pending_base = pending.get("base_revid") or ""
                pending_created = pending.get("created_at") or ""
                mode = "new article" if pending.get("create_new") else f"base {pending_base}"
                print(f"  pending_commit:  {mode}  ({pending_created})")
                if pending_summary:
                    print(f"  pending_summary: {pending_summary}")
            if merge_state:
                merge_base = merge_state.get("base_revid", "")
                merge_upstream = merge_state.get("upstream_revid", "")
                print(f"  merge_state:     base {merge_base} -> upstream {merge_upstream}")
            print()
            continue

        states = []
        details = []
        if merge_state:
            merge_base = merge_state.get("base_revid", "")
            merge_upstream = merge_state.get("upstream_revid", "")
            states.append("merging")
            details.append(f"base {merge_base} -> upstream {merge_upstream}")
        if pending:
            pending_summary = str(pending.get("summary") or "").strip()
            states.append("pending")
            if pending_summary:
                details.append(pending_summary)
        if not os.path.exists(local):
            states.append("missing")
        elif modified is True:
            states.append("modified")
        if upstream_ref is None:
            states.append("unfetched")
        elif base_ref is None:
            states.append("unmerged")
        elif int(base_ref) != int(upstream_ref):
            states.append("behind")
            details.append(f"base {base_ref} -> upstream {upstream_ref}")

        if states or key_filter:
            state = ",".join(states) if states else "clean"
            detail = "  " + "; ".join(details) if details else ""
            rows.append(f"{state:<18} {key}  {local}{detail}")

    if not verbose:
        if rows:
            print("\n".join(rows))
        else:
            print("All tracked articles clean.")


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
    p_add = sub.add_parser("add", help="Register a new article by URL or page name")
    p_add.add_argument("article", metavar="URL_OR_NAME",
                       help="Full wiki page URL or wiki page title/key")

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

    # commit
    p_commit = sub.add_parser("commit", help="Snapshot local edits as a pending wiki edit")
    p_commit.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")
    p_commit.add_argument("--new", action="store_true",
                          help="Commit a new article that does not have a base revid")
    p_commit.add_argument("--amend", action="store_true",
                          help="Replace the existing pending commit for this article")
    p_commit.add_argument("--allow-empty", action="store_true",
                          help="Allow a pending commit whose content matches the base")
    p_commit.add_argument("-m", "--message", help="Edit summary (skips editor prompt)")

    # push
    p_push = sub.add_parser("push", help="Submit pending local commits back to the wiki")
    p_push.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")
    p_push.add_argument("--dry-run", action="store_true", help="Preview without pushing")

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

    # restore
    p_restore = sub.add_parser("restore", help="Restore local file from refs/base")
    p_restore.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")
    p_restore.add_argument("--dry-run", action="store_true",
                           help="Preview restore without writing")
    p_restore.add_argument("--discard-commit", action="store_true",
                           help="Also discard any pending local commit")
    p_restore.add_argument("--abort-merge", action="store_true",
                           help="Clear merge-conflict state after restoring refs/base")

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

    # migrate
    p_migrate = sub.add_parser("migrate",
                               help="Migrate legacy article entries and local paths")
    p_migrate.add_argument("article", metavar="ARTICLE", nargs="?",
                           help="Article key (omit to migrate all)")
    p_migrate.add_argument("--dry-run", action="store_true",
                           help="Preview migrations without writing")
    p_migrate.add_argument("--yes", action="store_true",
                           help="Apply risky migrations without prompting")

    # status
    p_status = sub.add_parser("status", help="Show sync state of tracked articles")
    p_status.add_argument("article", metavar="ARTICLE", nargs="?",
                          help="Article key (omit to show all)")
    p_status.add_argument("-v", "--verbose", action="store_true",
                          help="Show detailed refs, URLs, and metadata")

    args = ap.parse_args()

    if not args.subcommand:
        ap.print_help()
        sys.exit(0)

    config_path = args.config
    if args.subcommand == "init":
        run_init(args, config_path)
        return

    config = load_config(config_path)

    if args.subcommand == "add":
        run_add(args, config, config_path)
    elif args.subcommand == "checkout":
        run_checkout(args, config, config_path)
    elif args.subcommand == "fetch":
        run_fetch(args, config, config_path)
    elif args.subcommand == "commit":
        run_commit(args, config, config_path)
    elif args.subcommand == "push":
        run_push(args, config, config_path)
    elif args.subcommand == "diff":
        run_diff(args, config, config_path)
    elif args.subcommand == "difftool":
        run_difftool(args, config, config_path)
    elif args.subcommand == "merge":
        run_merge(args, config, config_path)
    elif args.subcommand == "restore":
        run_restore(args, config, config_path)
    elif args.subcommand == "log":
        run_log(args, config, config_path)
    elif args.subcommand == "show":
        run_show(args, config, config_path)
    elif args.subcommand == "fsck":
        run_fsck(args, config, config_path)
    elif args.subcommand == "migrate":
        run_migrate(args, config, config_path)
    elif args.subcommand == "status":
        run_status(args, config, config_path)


if __name__ == "__main__":
    main()
