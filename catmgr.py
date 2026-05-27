#!/usr/bin/env python3
# Copyright (c) 2026 Rob Lanphier and contributors
# SPDX-License-Identifier: MIT
# See LICENSE for details.
"""
catmgr.py - cache and inspect target-wiki category names.

The cache belongs to the mwsync working directory:

  _cache/categories/manifest.json
  _cache/categories/allcategories.jsonl
  _cache/categories/category-pages.jsonl
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

import mwsync

CATEGORY_CACHE_DIR = os.path.join("_cache", "categories")
MANIFEST_PATH = os.path.join(CATEGORY_CACHE_DIR, "manifest.json")
ALLCATEGORIES_PATH = os.path.join(CATEGORY_CACHE_DIR, "allcategories.jsonl")
CATEGORY_PAGES_PATH = os.path.join(CATEGORY_CACHE_DIR, "category-pages.jsonl")
CATMAP_PATH = "catmap.yaml"
ENWIKI_API = "https://en.wikipedia.org/w/api.php"
CATEGORY_LINK_RE = re.compile(r"\[\[\s*[Cc]ategory\s*:[^\]\n]+\]\]")
CAT_MAIN_RE = re.compile(r"\{\{\s*[Cc]at main\s*\|([^{}\n|]+)(?:\|[^{}\n]*)?\}\}")
REDIRECT_RE = re.compile(r"\s*#REDIRECT\s*\[\[([^\]]+)\]\]", re.IGNORECASE)
BEHAVIOR_SWITCH_RE = re.compile(r"^__(?:HIDDENCAT|EXPECTUNUSEDCATEGORY|NOGALLERY)__\s*$",
                                re.IGNORECASE)


def normalize_category_name(name: str) -> str:
    raw = name.strip()
    if raw.lower().startswith("category:"):
        raw = raw.split(":", 1)[1]
    raw = raw.replace("_", " ").strip()
    if raw:
        raw = raw[0].upper() + raw[1:]
    return raw


def _api_get(api_base: str, params: dict) -> dict:
    url = api_base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": mwsync.USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        error = data["error"]
        code = error.get("code", "unknown")
        info = error.get("info", "unknown API error")
        raise ValueError(f"MediaWiki API error ({code}): {info}")
    return data


def _fetch_allcategories(api_base: str) -> list[dict]:
    rows = []
    continuation = {}
    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "allcategories",
            "aclimit": "max",
            "acprop": "size|hidden",
        }
        params.update(continuation)
        data = _api_get(api_base, params)
        for row in data.get("query", {}).get("allcategories", []):
            name = normalize_category_name(row.get("*", ""))
            if not name:
                continue
            rows.append({
                "name": name,
                "size": int(row.get("size") or 0),
                "pages": int(row.get("pages") or 0),
                "files": int(row.get("files") or 0),
                "subcats": int(row.get("subcats") or 0),
                "hidden": "hidden" in row,
            })
        continuation = data.get("continue")
        if not continuation:
            break
    return sorted(rows, key=lambda item: item["name"].lower())


def _fetch_category_page_batch(api_base: str, filterredir: str) -> list[dict]:
    rows = []
    continuation = {}
    while True:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "list": "allpages",
            "apnamespace": "14",
            "aplimit": "max",
            "apfilterredir": filterredir,
        }
        params.update(continuation)
        data = _api_get(api_base, params)
        for row in data.get("query", {}).get("allpages", []):
            title = row.get("title", "")
            name = normalize_category_name(title)
            if not name:
                continue
            rows.append({
                "name": name,
                "title": title,
                "pageid": int(row.get("pageid") or 0),
            })
        continuation = data.get("continue")
        if not continuation:
            break
    return rows


def _batched(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _resolve_redirect_targets(api_base: str, redirect_rows: list[dict]) -> dict[str, str]:
    targets = {}
    for batch in _batched(redirect_rows, 50):
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "info",
            "redirects": "1",
            "titles": "|".join(row["title"] for row in batch),
        }
        data = _api_get(api_base, params)
        for row in data.get("query", {}).get("redirects", []):
            source_name = normalize_category_name(row.get("from", ""))
            target_name = normalize_category_name(row.get("to", ""))
            if source_name and target_name:
                targets[source_name] = target_name
    return targets


def _fetch_category_pages(api_base: str) -> list[dict]:
    rows_by_name = {}

    for row in _fetch_category_page_batch(api_base, "nonredirects"):
        rows_by_name[row["name"]] = {
            "name": row["name"],
            "title": row["title"],
            "pageid": row["pageid"],
            "redirect": False,
        }

    redirect_rows = _fetch_category_page_batch(api_base, "redirects")
    redirect_targets = _resolve_redirect_targets(api_base, redirect_rows)
    for row in redirect_rows:
        item = {
            "name": row["name"],
            "title": row["title"],
            "pageid": row["pageid"],
            "redirect": True,
        }
        target = redirect_targets.get(row["name"])
        if target:
            item["redirect_target"] = target
        rows_by_name[row["name"]] = item

    return sorted(rows_by_name.values(), key=lambda item: item["name"].lower())


def _write_json(path: str, data: dict) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not mwsync._atomic_write(path, content):
        sys.exit(1)


def _write_jsonl(path: str, rows: list[dict]) -> None:
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                      for row in rows)
    if not mwsync._atomic_write(path, content):
        sys.exit(1)


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("Category cache not found. Run: catmgr.py fetch", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        sys.exit(1)
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: str) -> list[dict]:
    rows = []
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
                    rows.append(item)
    except FileNotFoundError:
        print("Category cache not found. Run: catmgr.py fetch", file=sys.stderr)
        sys.exit(1)
    return rows


def _load_cache() -> tuple[dict, list[dict], list[dict]]:
    return (
        _read_json(MANIFEST_PATH),
        _read_jsonl(ALLCATEGORIES_PATH),
        _read_jsonl(CATEGORY_PAGES_PATH),
    )


def load_catmap() -> dict[str, object]:
    if mwsync.yaml is None:
        return {}
    if not os.path.exists(CATMAP_PATH):
        return {}
    try:
        with open(CATMAP_PATH, "r", encoding="utf-8") as f:
            data = mwsync.yaml.safe_load(f)
    except Exception as e:
        print(f"Error reading {CATMAP_PATH}: {e}", file=sys.stderr)
        sys.exit(1)
    if data is None:
        return {}
    if not isinstance(data, dict):
        print(f"Error: {CATMAP_PATH} must contain a YAML mapping.", file=sys.stderr)
        sys.exit(1)
    raw_mappings = data.get("mappings", {})
    if raw_mappings is None:
        return {}
    if not isinstance(raw_mappings, dict):
        print(f"Error: {CATMAP_PATH}: 'mappings' must be a mapping.", file=sys.stderr)
        sys.exit(1)

    mappings: dict[str, object] = {}
    for key, value in raw_mappings.items():
        if value is not None and not isinstance(value, str):
            print(f"Error: {CATMAP_PATH}: mapping for {key!r} must be string or null.",
                  file=sys.stderr)
            sys.exit(1)
        normalized_key = normalize_category_name(str(key))
        if not normalized_key:
            continue
        mappings[normalized_key] = (
            None if value is None else normalize_category_name(value)
        )
    return mappings


def save_catmap(mapping: dict[str, object]) -> None:
    sorted_keys = sorted(mapping.keys(), key=str.lower)
    body = {"mappings": {key: mapping[key] for key in sorted_keys}}
    if not mwsync.save_config(body, CATMAP_PATH):
        sys.exit(1)


def load_category_cache() -> tuple[set[str], set[str], dict[str, str]] | None:
    """Return (canonical_pages, used_categories, redirects), or None if absent.

    This optional loader is for import/seeding flows where a missing cache
    should degrade prompts rather than fail the whole command.
    """
    if not os.path.exists(MANIFEST_PATH):
        return None

    canonical: set[str] = set()
    used: set[str] = set()
    redirects: dict[str, str] = {}

    if os.path.exists(CATEGORY_PAGES_PATH):
        try:
            with open(CATEGORY_PAGES_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    name = row.get("name")
                    if not isinstance(name, str) or not name:
                        continue
                    if row.get("redirect"):
                        target = row.get("redirect_target")
                        if isinstance(target, str) and target:
                            redirects[name] = target
                    else:
                        canonical.add(name)
        except Exception as e:
            print(f"Warning: could not read {CATEGORY_PAGES_PATH}: {e}",
                  file=sys.stderr)
            return None

    if os.path.exists(ALLCATEGORIES_PATH):
        try:
            with open(ALLCATEGORIES_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    name = row.get("name")
                    if isinstance(name, str) and name:
                        used.add(name)
        except Exception as e:
            print(f"Warning: could not read {ALLCATEGORIES_PATH}: {e}",
                  file=sys.stderr)
            return None

    return canonical, used, redirects


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from e
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def run_fetch(args, config: dict) -> None:
    api_base = mwsync.get_api_base(config)
    print(f"# Fetching category table from {api_base}...", file=sys.stderr)
    try:
        allcategories = _fetch_allcategories(api_base)
        print(f"# Fetching category pages from {api_base}...", file=sys.stderr)
        category_pages = _fetch_category_pages(api_base)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    manifest = {
        "api_base": api_base,
        "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "allcategories_count": len(allcategories),
        "category_pages_count": len(category_pages),
        "category_redirects_count": sum(1 for row in category_pages
                                        if row.get("redirect")),
    }

    _write_jsonl(ALLCATEGORIES_PATH, allcategories)
    _write_jsonl(CATEGORY_PAGES_PATH, category_pages)
    _write_json(MANIFEST_PATH, manifest)
    print(f"# Wrote {ALLCATEGORIES_PATH} ({len(allcategories)} categories)", file=sys.stderr)
    print(f"# Wrote {CATEGORY_PAGES_PATH} ({len(category_pages)} category pages)",
          file=sys.stderr)
    print(f"# Wrote {MANIFEST_PATH}", file=sys.stderr)


def run_status(args, config: dict) -> None:
    manifest = _read_json(MANIFEST_PATH)
    print(f"api_base: {manifest.get('api_base', '')}")
    print(f"fetched_at: {manifest.get('fetched_at', '')}")
    print(f"allcategories_count: {manifest.get('allcategories_count', 0)}")
    print(f"category_pages_count: {manifest.get('category_pages_count', 0)}")
    print(f"category_redirects_count: {manifest.get('category_redirects_count', 0)}")


def run_list(args, config: dict) -> None:
    _manifest, allcategories, category_pages = _load_cache()
    rows = _category_index(allcategories, category_pages)
    rows = [row for row in rows if _matches_list_filters(row, args)]
    for row in rows:
        if args.verbose:
            print(_format_list_verbose(row))
        else:
            print(row["name"])


def _category_index(allcategories: list[dict], category_pages: list[dict]) -> list[dict]:
    rows = {}
    for item in allcategories:
        name = item.get("name")
        if not name:
            continue
        rows[name] = {
            "name": name,
            "pages": int(item.get("pages") or 0),
            "subcats": int(item.get("subcats") or 0),
            "files": int(item.get("files") or 0),
            "size": int(item.get("size") or 0),
            "hidden": bool(item.get("hidden")),
            "has_cat_page": False,
            "redirect": False,
            "redirect_target": "",
        }

    for item in category_pages:
        name = item.get("name")
        if not name:
            continue
        row = rows.setdefault(name, {
            "name": name,
            "pages": 0,
            "subcats": 0,
            "files": 0,
            "size": 0,
            "hidden": False,
            "has_cat_page": False,
            "redirect": False,
            "redirect_target": "",
        })
        row["has_cat_page"] = True
        row["redirect"] = bool(item.get("redirect"))
        row["redirect_target"] = item.get("redirect_target", "")

    return sorted(rows.values(), key=lambda item: item["name"].lower())


def _matches_list_filters(row: dict, args) -> bool:
    has_cat_page = row["has_cat_page"]
    if args.has_cat_page == "true" and not has_cat_page:
        return False
    if args.has_cat_page == "false" and has_cat_page:
        return False

    pages = row["pages"]
    if args.has_pages is not None and pages != args.has_pages:
        return False
    if args.min_pages is not None and pages < args.min_pages:
        return False
    if args.max_pages is not None and pages > args.max_pages:
        return False
    return True


def _format_list_verbose(row: dict) -> str:
    fields = [
        row["name"],
        f"cat_page={'yes' if row['has_cat_page'] else 'no'}",
        f"pages={row['pages']}",
        f"subcats={row['subcats']}",
        f"files={row['files']}",
        f"hidden={'yes' if row['hidden'] else 'no'}",
    ]
    if row["redirect"]:
        target = row.get("redirect_target") or ""
        fields.append(f"redirect=yes target={target}")
    elif row["has_cat_page"]:
        fields.append("redirect=no")
    return "\t".join(fields)


def _validate_list_args(args, parser: argparse.ArgumentParser) -> None:
    if args.min_pages is not None and args.max_pages is not None:
        if args.min_pages > args.max_pages:
            parser.error("--min-pages cannot be greater than --max-pages")
    if args.has_pages is not None:
        if args.min_pages is not None and args.has_pages < args.min_pages:
            parser.error("--has-pages cannot be less than --min-pages")
        if args.max_pages is not None and args.has_pages > args.max_pages:
            parser.error("--has-pages cannot be greater than --max-pages")


def _category_page_maps(
        allcategories: list[dict],
        category_pages: list[dict]) -> tuple[dict[str, dict], dict[str, dict], dict[str, str]]:
    used = {row["name"]: row for row in allcategories if row.get("name")}
    pages = {row["name"]: row for row in category_pages if row.get("name")}
    redirects = {
        row["name"]: row["redirect_target"]
        for row in category_pages
        if row.get("name") and row.get("redirect") and row.get("redirect_target")
    }
    return used, pages, redirects


def extract_category_links(source: str) -> list[tuple[str, str | None]]:
    """Return (raw_name, sortkey) tuples from [[Category:...]] links."""
    result = []
    for link in CATEGORY_LINK_RE.findall(source):
        inner = link[2:-2]
        _prefix, _sep, payload = inner.partition(":")
        if "|" in payload:
            name, sortkey = payload.split("|", 1)
        else:
            name, sortkey = payload, None
        result.append((name.strip(), sortkey))
    return result


def _strip_category_links(source: str) -> str:
    lines = []
    for line in source.splitlines():
        if BEHAVIOR_SWITCH_RE.match(line.strip()):
            continue
        stripped = CATEGORY_LINK_RE.sub("", line).rstrip()
        if stripped.strip():
            lines.append(stripped)
    return "\n".join(lines).strip()


def _replace_cat_main_templates(source: str) -> str:
    def replacement(match: re.Match) -> str:
        title = match.group(1).strip()
        if not title:
            return ""
        return f"See [[{title}]] for the main article about this topic."

    return CAT_MAIN_RE.sub(replacement, source)


def format_category_link(name: str, sortkey: str | None = None) -> str:
    if sortkey is not None:
        return f"[[Category:{name}|{sortkey}]]"
    return f"[[Category:{name}]]"


def _resolve_redirect(name: str, redirects: dict[str, str]) -> tuple[str, bool]:
    seen: set[str] = set()
    current = name
    for _ in range(8):
        if current not in redirects or current in seen:
            break
        seen.add(current)
        current = redirects[current]
    return current, current != name


def _print_redirect_note(source: str, target: str) -> None:
    print(f'  "{source}" is a redirect on Electowiki to "{target}"; '
          f'using "{target}".')


def _input_with_completion(prompt: str, candidates: set[str]) -> str:
    """Read a line from stdin with case-insensitive prefix completion."""
    try:
        import readline
    except ImportError:
        return input(prompt)

    sorted_candidates = sorted(candidates, key=str.lower)

    def completer(text: str, state: int) -> str | None:
        text_lower = text.lower()
        matches = [candidate for candidate in sorted_candidates
                   if candidate.lower().startswith(text_lower)]
        return matches[state] if state < len(matches) else None

    old_completer = readline.get_completer()
    old_delims = readline.get_completer_delims()
    readline.set_completer(completer)
    readline.set_completer_delims("")
    readline.parse_and_bind("tab: complete")
    try:
        return input(prompt)
    finally:
        readline.set_completer(old_completer)
        readline.set_completer_delims(old_delims)


def _prompt_category_action(name: str, sortkey: str | None,
                            cache_status: str,
                            candidates: set[str]) -> tuple[str, str | None]:
    sortkey_note = f"  (sortkey: {sortkey!r})" if sortkey else ""
    print()
    print(f"Category not resolved: {name}{sortkey_note}")
    print(f"  cache: {cache_status}")
    print("  [m] map and save  [d] drop and save  [K] keep and save")
    print("  [k] keep once     [s] skip once")
    while True:
        try:
            choice = input("  choice: ").strip()
        except EOFError:
            return "skip", None
        if choice == "m":
            try:
                new_name = _input_with_completion(
                    "  new category name: ", candidates).strip()
            except EOFError:
                return "skip", None
            if not new_name:
                print("  empty name; please pick again.")
                continue
            return "map", normalize_category_name(new_name)
        if choice == "d":
            return "drop", None
        if choice == "K":
            return "keep_save", None
        if choice == "k":
            return "keep_once", None
        if choice == "s":
            return "skip", None
        print("  unrecognized choice; valid options: m, d, K, k, s")


def _category_cache_status(name: str,
                           canonical_pages: set[str] | None,
                           used_categories: set[str] | None) -> str:
    if canonical_pages is None:
        return "cache missing"
    if used_categories is not None and name in used_categories:
        return "used on Electowiki but no category page"
    return "absent from Electowiki cache"


def category_plan_lines(source_links: list[tuple[str, str | None]],
                        catmap: dict[str, object],
                        cache: tuple[set[str], set[str], dict[str, str]] | None,
                        is_tty: bool) -> list[str]:
    """Describe source-category handling before interactive prompts begin."""
    if cache is None:
        canonical_pages = used_categories = None
        redirects: dict[str, str] = {}
    else:
        canonical_pages, used_categories, redirects = cache

    rows = []
    unresolved: set[str] = set()
    for raw_name, sortkey in source_links:
        normalized = normalize_category_name(raw_name)
        if not normalized:
            continue

        sortkey_note = f" | sortkey={sortkey!r}" if sortkey else ""
        if normalized in catmap:
            value = catmap[normalized]
            if value is None:
                disposition = "drop (catmap.yaml)"
            else:
                value_str = str(value)
                resolved, via_redir = _resolve_redirect(value_str, redirects)
                if value_str == normalized and not via_redir:
                    disposition = "keep (catmap.yaml)"
                elif via_redir:
                    disposition = (f"use {resolved} "
                                   f"(catmap.yaml via Electowiki redirect)")
                else:
                    disposition = f"use {value_str} (catmap.yaml)"
        elif normalized in redirects:
            resolved, _via_redir = _resolve_redirect(normalized, redirects)
            disposition = f"use {resolved} (Electowiki redirect)"
        elif canonical_pages is not None and normalized in canonical_pages:
            disposition = "keep (Electowiki category page)"
        else:
            cache_status = _category_cache_status(
                normalized, canonical_pages, used_categories)
            if is_tty:
                disposition = f"ask ({cache_status})"
                unresolved.add(normalized)
            else:
                disposition = f"drop; review-needed ({cache_status})"

        rows.append(f"  - {normalized}{sortkey_note}: {disposition}")

    if not rows:
        return ["Source categories: none."]

    lines = [f"Source categories ({len(rows)}):"]
    lines.extend(rows)
    if is_tty and unresolved:
        suffix = "" if len(unresolved) == 1 else "s"
        lines.append(f"Interactive category decisions needed: "
                     f"{len(unresolved)} unique category name{suffix}.")
    return lines


def resolve_categories(
        source_links: list[tuple[str, str | None]],
        catmap: dict[str, object],
        cache: tuple[set[str], set[str], dict[str, str]] | None,
        is_tty: bool) -> tuple[list[str], list[dict], int]:
    """Resolve source category links against catmap and target-wiki cache."""
    output: list[str] = []
    outcomes: list[dict] = []
    new_entries = 0
    cache_warned = False

    if cache is None:
        canonical_pages = used_categories = None
        redirects: dict[str, str] = {}
    else:
        canonical_pages, used_categories, redirects = cache

    candidates: set[str] = set()
    if canonical_pages:
        candidates |= canonical_pages
    if used_categories:
        candidates |= used_categories
    candidates |= set(redirects.keys())
    candidates |= set(redirects.values())
    candidates |= {v for v in catmap.values() if isinstance(v, str)}

    def emit(name: str, sortkey: str | None,
             outcome: dict, source_for_note: str | None = None) -> None:
        resolved, via_redir = _resolve_redirect(name, redirects)
        if via_redir:
            _print_redirect_note(source_for_note or name, resolved)
            outcome["via_redirect"] = True
            outcome["target"] = resolved
        output.append(format_category_link(resolved, sortkey))

    for raw_name, sortkey in source_links:
        normalized = normalize_category_name(raw_name)
        if not normalized:
            continue

        if normalized in catmap:
            value = catmap[normalized]
            if value is None:
                outcomes.append({"name": normalized, "action": "drop"})
                continue
            value_str = str(value)
            action = "keep" if value_str == normalized else "map"
            outcome = {"name": normalized, "action": action,
                       "target": value_str}
            emit(value_str, sortkey, outcome, source_for_note=value_str)
            outcomes.append(outcome)
            continue

        if normalized in redirects:
            outcome = {"name": normalized, "action": "keep"}
            emit(normalized, sortkey, outcome, source_for_note=normalized)
            outcomes.append(outcome)
            continue

        if canonical_pages is not None and normalized in canonical_pages:
            outcome = {"name": normalized, "action": "keep"}
            emit(normalized, sortkey, outcome)
            outcomes.append(outcome)
            continue

        if canonical_pages is None and not cache_warned:
            print("Category cache not found; run catmgr.py fetch for "
                  "better suggestions.", file=sys.stderr)
            cache_warned = True
        cache_status = _category_cache_status(
            normalized, canonical_pages, used_categories)

        if not is_tty:
            outcomes.append({"name": normalized, "action": "review"})
            continue

        action, target = _prompt_category_action(normalized, sortkey,
                                                 cache_status, candidates)
        if action == "map":
            resolved, via_redir = _resolve_redirect(target, redirects)
            if via_redir:
                _print_redirect_note(target, resolved)
            output.append(format_category_link(resolved, sortkey))
            catmap[normalized] = resolved
            save_catmap(catmap)
            new_entries += 1
            candidates.add(resolved)
            outcome = {"name": normalized, "action": "map", "target": resolved}
            if via_redir:
                outcome["via_redirect"] = True
            outcomes.append(outcome)
        elif action == "drop":
            catmap[normalized] = None
            save_catmap(catmap)
            new_entries += 1
            outcomes.append({"name": normalized, "action": "drop"})
        elif action == "keep_save":
            output.append(format_category_link(normalized, sortkey))
            catmap[normalized] = normalized
            save_catmap(catmap)
            new_entries += 1
            candidates.add(normalized)
            outcomes.append({"name": normalized, "action": "keep"})
        elif action == "keep_once":
            output.append(format_category_link(normalized, sortkey))
            outcomes.append({"name": normalized, "action": "keep"})
        elif action == "skip":
            outcomes.append({"name": normalized, "action": "skip"})

    return output, outcomes, new_entries


def category_summary_lines(outcomes: list[dict], new_entries: int) -> list[str]:
    if not outcomes:
        return ["  No categories found in source."]
    counts = {"keep": 0, "map": 0, "drop": 0, "skip": 0, "review": 0}
    redirect_count = 0
    for outcome in outcomes:
        action = outcome.get("action")
        if action in counts:
            counts[action] += 1
        if outcome.get("via_redirect"):
            redirect_count += 1
    parts = []
    if counts["keep"]:
        parts.append(f"{counts['keep']} kept")
    if counts["map"]:
        parts.append(f"{counts['map']} mapped")
    if counts["drop"]:
        parts.append(f"{counts['drop']} dropped")
    if counts["skip"]:
        parts.append(f"{counts['skip']} skipped")
    if counts["review"]:
        parts.append(f"{counts['review']} review-needed")
    lines = [f"  Categories: {', '.join(parts)}."]
    if redirect_count:
        suffix = "" if redirect_count == 1 else "s"
        lines.append(f"  {redirect_count} routed via Electowiki redirect{suffix}.")
    if new_entries:
        suffix = "y" if new_entries == 1 else "ies"
        lines.append(f"  Wrote {new_entries} new catmap.yaml entr{suffix}.")
    if counts["review"]:
        lines.append("  Review-needed (re-run interactively or edit catmap.yaml):")
        for outcome in outcomes:
            if outcome.get("action") == "review":
                lines.append(f"    - {outcome['name']}")
    return lines


def _is_redirect_wikitext(wikitext: str) -> tuple[bool, str | None]:
    match = REDIRECT_RE.match(wikitext)
    if not match:
        return False, None
    target = match.group(1).split("|", 1)[0].strip()
    return True, target or None


def _fetch_enwiki_category_page(name: str) -> dict:
    title = f"Category:{name}"
    try:
        page = mwsync._fetch_page(title, ENWIKI_API)
    except Exception as e:
        print(f"Error: failed to fetch enwiki {title}: {e}", file=sys.stderr)
        sys.exit(1)

    is_redirect, target = _is_redirect_wikitext(page["wikitext"])
    if is_redirect:
        print(f"Error: enwiki {title} is a redirect"
              + (f" to '{target}'." if target else "."), file=sys.stderr)
        if target:
            print(f"Re-run with the target category: catmgr.py seed \"{target}\"",
                  file=sys.stderr)
        sys.exit(1)
    page["title"] = title
    return page


def _expand_seed_sources(args) -> tuple[str, str]:
    presets = {
        "manual": ("manual", "none"),
        "enwiki": ("enwiki", "enwiki"),
    }
    parents_from, prose_from = presets[args.source_preset]
    if args.parents_from is not None:
        parents_from = args.parents_from
    if args.prose_from is not None:
        prose_from = args.prose_from
    return parents_from, prose_from


def _seed_prose_from_enwiki(page: dict) -> str:
    source = _replace_cat_main_templates(page.get("wikitext", ""))
    return _strip_category_links(source)


def _resolve_seed_parent_links(
        source_links: list[tuple[str, str | None]],
        catmap: dict[str, object],
        cache: tuple[set[str], set[str], dict[str, str]] | None,
        is_tty: bool) -> tuple[list[str], list[dict], int]:
    for line in category_plan_lines(source_links, catmap, cache, is_tty):
        print(line, file=sys.stderr)
    return resolve_categories(source_links, catmap, cache, is_tty)


def _seed_article_fields(config: dict, name: str) -> tuple[str, dict]:
    namespace_map = mwsync._load_namespace_map(config, fetch=False, allow_fallback=True)
    parts = mwsync._parse_title_parts(f"Category:{name}", namespace_map)
    key = mwsync._key_for_title_parts(parts)
    fields = {
        "title": parts["title"],
        "url": mwsync._article_url_from_title(config, parts["title"]),
        "local": mwsync._local_for_title_parts(parts),
        "namespace": int(parts["namespace"]),
        "namespace_name": parts["namespace_name"],
        "dbkey": parts["dbkey"],
    }
    return key, fields


def _build_seed_text(name: str,
                     parent_links: list[str],
                     prose: str = "",
                     attribution: str = "") -> str:
    lines = []
    if prose.strip():
        lines.append(prose.strip())
    if attribution.strip():
        if lines:
            lines.append("")
        lines.append(attribution.strip())
    if parent_links:
        if lines:
            lines.append("")
        lines.extend(parent_links)
    return "\n".join(lines) + "\n"


def _dedupe_category_links(links: list[str]) -> list[str]:
    seen = set()
    out = []
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        out.append(link)
    return out


def run_seed(args, config: dict, config_path: str) -> None:
    name = normalize_category_name(args.name)
    if not name:
        print("Error: category name cannot be empty.", file=sys.stderr)
        sys.exit(1)
    parents_from, prose_from = _expand_seed_sources(args)

    allcategories: list[dict] = []
    category_pages: list[dict] = []
    if os.path.exists(MANIFEST_PATH):
        _manifest, allcategories, category_pages = _load_cache()
    _used, pages, _redirects = _category_page_maps(allcategories, category_pages)
    page_row = pages.get(name)
    if page_row:
        if page_row.get("redirect"):
            target = page_row.get("redirect_target", "")
            print(f"# Category:{name} exists in the target wiki cache as a redirect"
                  f"{f' to Category:{target}' if target else ''}.",
                  file=sys.stderr)
        else:
            print(f"# Category:{name} already exists in the target wiki cache.",
                  file=sys.stderr)

    enwiki_page = None
    if parents_from == "enwiki" or prose_from == "enwiki":
        print(f"# Fetching enwiki Category:{name}...", file=sys.stderr)
        enwiki_page = _fetch_enwiki_category_page(name)

    catmap = load_catmap()
    cache = load_category_cache()
    parent_source_links = [(parent, None) for parent in args.parent]
    if parents_from == "enwiki" and enwiki_page is not None:
        parent_source_links.extend(extract_category_links(enwiki_page["wikitext"]))

    parent_links: list[str] = []
    outcomes: list[dict] = []
    new_entries = 0
    if parent_source_links:
        is_tty = sys.stdin.isatty()
        parent_links, outcomes, new_entries = _resolve_seed_parent_links(
            parent_source_links, catmap, cache, is_tty)
        parent_links = _dedupe_category_links(parent_links)

    prose = ""
    attribution = ""
    if prose_from == "enwiki" and enwiki_page is not None:
        prose = _seed_prose_from_enwiki(enwiki_page)
        attribution = (f"{{{{Fromwikipedia|Category:{name}|"
                       f"oldid={enwiki_page['revid']}}}}}")

    key, fields = _seed_article_fields(config, name)
    articles = config.setdefault("wiki", {}).setdefault("articles", {})
    if key in articles:
        existing_local = articles[key].get("local", key + ".mw")
        if existing_local != fields["local"]:
            print(f"Error: article '{key}' is already registered with a different "
                  f"local file: {existing_local}", file=sys.stderr)
            print("Refusing to overwrite; resolve the existing entry first.",
                  file=sys.stderr)
            sys.exit(1)
        if not args.force:
            print(f"Error: article '{key}' is already registered in {config_path}.",
                  file=sys.stderr)
            print("Use --force to overwrite the local seed.", file=sys.stderr)
            sys.exit(1)

    local = fields["local"]
    local_matches = [
        article_key for article_key, art in articles.items()
        if art.get("local", article_key + ".mw") == local
    ]
    conflicting_matches = [article_key for article_key in local_matches
                           if article_key != key]
    if conflicting_matches:
        print(f"Error: local file '{local}' is already registered in {config_path}.",
              file=sys.stderr)
        print(f"Matches: {', '.join(conflicting_matches)}", file=sys.stderr)
        sys.exit(1)
    if local_matches and not args.force:
        print(f"Error: local file '{local}' is already registered in {config_path}.",
              file=sys.stderr)
        print("Use --force to overwrite the local seed.", file=sys.stderr)
        sys.exit(1)

    local_preexisted = os.path.exists(local)
    if local_preexisted and not args.force:
        print(f"Error: local file already exists: {local}", file=sys.stderr)
        print("Use --force to overwrite the local seed.", file=sys.stderr)
        sys.exit(1)

    text = _build_seed_text(name, parent_links, prose, attribution)
    if not mwsync._atomic_write(local, text):
        sys.exit(1)
    articles[key] = fields
    if not mwsync.save_config(config, config_path):
        if not local_preexisted:
            try:
                os.unlink(local)
            except OSError:
                pass
        sys.exit(1)

    print(f"# Seeded Category:{name}", file=sys.stderr)
    print(f"#   key:   {key}", file=sys.stderr)
    print(f"#   local: {local}", file=sys.stderr)
    if fields.get("url"):
        print(f"#   url:   {fields['url']}", file=sys.stderr)
    if parent_links:
        print(f"#   parent categories: {len(parent_links)}", file=sys.stderr)
    else:
        print("#   parent categories: none", file=sys.stderr)
    if prose_from == "enwiki" and enwiki_page is not None:
        print(f"#   enwiki source revid: {enwiki_page['revid']}", file=sys.stderr)
    if parent_source_links:
        for line in category_summary_lines(outcomes, new_entries):
            print(f"# {line}", file=sys.stderr)
    print(f"# Review {local}, then use mwsync.py commit/push.", file=sys.stderr)


def run_find(args, config: dict) -> None:
    needle = args.text.lower()
    _manifest, allcategories, category_pages = _load_cache()
    names = {row["name"] for row in allcategories if row.get("name")}
    names.update(row["name"] for row in category_pages if row.get("name"))
    for name in sorted(names, key=str.lower):
        if needle in name.lower():
            print(name)


def run_check(args, config: dict) -> None:
    name = normalize_category_name(args.name)
    _manifest, allcategories, category_pages = _load_cache()
    used = {row["name"]: row for row in allcategories if row.get("name")}
    pages = {row["name"]: row for row in category_pages if row.get("name")}

    used_row = used.get(name)
    page_row = pages.get(name)

    print(f"Category:{name}")
    if page_row and page_row.get("redirect"):
        target = page_row.get("redirect_target", "")
        print(f"  category page: yes (redirect to \"{target}\")")
    else:
        print(f"  category page: {'yes' if page_row else 'no'}")
    print(f"  used category: {'yes' if used_row else 'no'}")
    if used_row:
        print(
            "  members: "
            f"{used_row.get('size', 0)} total, "
            f"{used_row.get('pages', 0)} pages, "
            f"{used_row.get('subcats', 0)} subcategories, "
            f"{used_row.get('files', 0)} files"
        )
        if used_row.get("hidden"):
            print("  hidden: yes")


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="catmgr.py",
        description="Cache and inspect target-wiki category names.",
    )
    ap.add_argument(
        "--config", default=mwsync.DEFAULT_CONFIG_PATH,
        help=f"Path to config file (default: {mwsync.DEFAULT_CONFIG_PATH})",
    )
    sub = ap.add_subparsers(dest="subcommand", help="Available subcommands")

    sub.add_parser("fetch", help="Refresh _cache/categories from the target wiki")
    sub.add_parser("status", help="Show category cache status")
    p_list = sub.add_parser("list", help="List cached category names")
    p_list.add_argument(
        "--has-cat-page",
        choices=["true", "false", "any"],
        default="any",
        help="Filter by whether a Category: page exists (default: any)",
    )
    p_list.add_argument(
        "--has-pages",
        type=_nonnegative_int,
        help="Filter to categories with exactly this many normal page members",
    )
    p_list.add_argument(
        "--min-pages",
        type=_nonnegative_int,
        help="Filter to categories with at least this many normal page members",
    )
    p_list.add_argument(
        "--max-pages",
        type=_nonnegative_int,
        help="Filter to categories with at most this many normal page members",
    )
    p_list.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Include cached counts and category-page status",
    )

    p_find = sub.add_parser("find", help="Search cached category names")
    p_find.add_argument("text", help="Case-insensitive search text")

    p_check = sub.add_parser("check", help="Check one category name")
    p_check.add_argument("name", help="Category name, with or without Category: prefix")

    p_seed = sub.add_parser("seed", help="Create a local starter category page")
    p_seed.add_argument("name", help="Category name, with or without Category: prefix")
    p_seed.add_argument(
        "--from",
        dest="source_preset",
        choices=["manual", "enwiki"],
        default="manual",
        help="Preset for parent/prose sources (default: manual)",
    )
    p_seed.add_argument(
        "--parents-from",
        choices=["manual", "enwiki"],
        help="Source for parent categories; overrides --from",
    )
    p_seed.add_argument(
        "--prose-from",
        choices=["none", "enwiki"],
        help="Source for starter prose; overrides --from",
    )
    p_seed.add_argument(
        "--parent",
        action="append",
        default=[],
        help="Parent category to include after resolving catmap/cache rules; repeatable",
    )
    p_seed.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite an existing local seed",
    )

    args = ap.parse_args()
    if not args.subcommand:
        ap.print_help()
        sys.exit(0)
    if args.subcommand == "list":
        _validate_list_args(args, ap)

    config = mwsync.load_config(args.config)

    if args.subcommand == "fetch":
        run_fetch(args, config)
    elif args.subcommand == "status":
        run_status(args, config)
    elif args.subcommand == "list":
        run_list(args, config)
    elif args.subcommand == "find":
        run_find(args, config)
    elif args.subcommand == "check":
        run_check(args, config)
    elif args.subcommand == "seed":
        run_seed(args, config, args.config)


if __name__ == "__main__":
    main()
