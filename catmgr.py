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
import sys
import urllib.parse
import urllib.request

import mwsync

CATEGORY_CACHE_DIR = os.path.join("_cache", "categories")
MANIFEST_PATH = os.path.join(CATEGORY_CACHE_DIR, "manifest.json")
ALLCATEGORIES_PATH = os.path.join(CATEGORY_CACHE_DIR, "allcategories.jsonl")
CATEGORY_PAGES_PATH = os.path.join(CATEGORY_CACHE_DIR, "category-pages.jsonl")
CATMAP_PATH = "catmap.yaml"


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


def _load_catmap() -> dict[str, object]:
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


def _format_category(name: str) -> str:
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


def _resolve_seed_parent(
        raw_name: str,
        catmap: dict[str, object],
        pages: dict[str, dict],
        used: dict[str, dict],
        redirects: dict[str, str]) -> tuple[str | None, str]:
    name = normalize_category_name(raw_name)
    if not name:
        return None, "empty"

    if name in catmap:
        value = catmap[name]
        if value is None:
            return None, "drop (catmap.yaml)"
        target = str(value)
        resolved, via_redirect = _resolve_redirect(target, redirects)
        if via_redirect:
            return resolved, f"use {resolved} (catmap.yaml via redirect)"
        if target == name:
            return target, "keep (catmap.yaml)"
        return target, f"use {target} (catmap.yaml)"

    if name in redirects:
        resolved, _via_redirect = _resolve_redirect(name, redirects)
        return resolved, f"use {resolved} (Electowiki redirect)"

    page_row = pages.get(name)
    if page_row and not page_row.get("redirect"):
        return name, "keep (Electowiki category page)"

    if name in used:
        return None, "unresolved: used on Electowiki but no category page"
    return None, "unresolved: absent from Electowiki cache"


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


def _build_seed_text(name: str, parent_links: list[str]) -> str:
    lines = [
        "<!-- Starter category page generated by catmgr.py seed. "
        "Review before pushing. -->",
    ]
    if parent_links:
        lines.append("")
        lines.extend(parent_links)
    return "\n".join(lines) + "\n"


def run_seed(args, config: dict, config_path: str) -> None:
    name = normalize_category_name(args.name)
    if not name:
        print("Error: category name cannot be empty.", file=sys.stderr)
        sys.exit(1)

    _manifest, allcategories, category_pages = _load_cache()
    used, pages, redirects = _category_page_maps(allcategories, category_pages)
    if name not in used:
        print(f"Error: Category:{name} is not a used category in the target wiki cache.",
              file=sys.stderr)
        print("Run 'catmgr.py list --has-cat-page=false --min-pages=1' "
              "to find seed candidates.", file=sys.stderr)
        sys.exit(1)
    page_row = pages.get(name)
    if page_row:
        if page_row.get("redirect"):
            target = page_row.get("redirect_target", "")
            print(f"Error: Category:{name} already exists as a redirect"
                  f"{f' to Category:{target}' if target else ''}.", file=sys.stderr)
        else:
            print(f"Error: Category:{name} already exists on the target wiki cache.",
                  file=sys.stderr)
        sys.exit(1)

    catmap = _load_catmap()
    parent_links: list[str] = []
    unresolved: list[tuple[str, str]] = []
    seen_parents: set[str] = set()
    for raw_parent in args.parent:
        resolved, status = _resolve_seed_parent(raw_parent, catmap, pages, used, redirects)
        parent_name = normalize_category_name(raw_parent)
        print(f"# parent {parent_name}: {status}", file=sys.stderr)
        if resolved:
            if resolved not in seen_parents:
                parent_links.append(_format_category(resolved))
                seen_parents.add(resolved)
        elif not status.startswith("drop"):
            unresolved.append((parent_name, status))

    if unresolved and not args.allow_unresolved_parents:
        print("Error: unresolved parent categories:", file=sys.stderr)
        for parent_name, status in unresolved:
            print(f"  - {parent_name}: {status}", file=sys.stderr)
        print("Use --allow-unresolved-parents to seed without them, or edit catmap.yaml.",
              file=sys.stderr)
        sys.exit(1)

    key, fields = _seed_article_fields(config, name)
    articles = config.setdefault("wiki", {}).setdefault("articles", {})
    if key in articles:
        print(f"Error: article '{key}' is already registered in {config_path}.",
              file=sys.stderr)
        sys.exit(1)

    local = fields["local"]
    local_matches = [
        article_key for article_key, art in articles.items()
        if art.get("local", article_key + ".mw") == local
    ]
    if local_matches:
        print(f"Error: local file '{local}' is already registered in {config_path}.",
              file=sys.stderr)
        print(f"Matches: {', '.join(local_matches)}", file=sys.stderr)
        sys.exit(1)
    if os.path.exists(local):
        print(f"Error: local file already exists: {local}", file=sys.stderr)
        sys.exit(1)

    text = _build_seed_text(name, parent_links)
    if not mwsync._atomic_write(local, text):
        sys.exit(1)
    articles[key] = fields
    if not mwsync.save_config(config, config_path):
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
        "--parent",
        action="append",
        default=[],
        help="Parent category to include after resolving catmap/cache rules; repeatable",
    )
    p_seed.add_argument(
        "--allow-unresolved-parents",
        action="store_true",
        help="Create the starter page even when a supplied parent cannot be resolved",
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
