#!/usr/bin/env python3
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
        return json.loads(resp.read().decode("utf-8"))


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


def _fetch_category_pages(api_base: str) -> list[dict]:
    rows = []
    continuation = {}
    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "allpages",
            "apnamespace": "14",
            "aplimit": "max",
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
                "missing": "missing" in row,
            })
        continuation = data.get("continue")
        if not continuation:
            break
    return sorted(rows, key=lambda item: item["name"].lower())


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


def run_fetch(args, config: dict) -> None:
    api_base = mwsync.get_api_base(config)
    print(f"# Fetching category table from {api_base}...", file=sys.stderr)
    allcategories = _fetch_allcategories(api_base)
    print(f"# Fetching category pages from {api_base}...", file=sys.stderr)
    category_pages = _fetch_category_pages(api_base)

    manifest = {
        "api_base": api_base,
        "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "allcategories_count": len(allcategories),
        "category_pages_count": len(category_pages),
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


def run_list(args, config: dict) -> None:
    _manifest, allcategories, category_pages = _load_cache()
    names = {row["name"] for row in allcategories if row.get("name")}
    names.update(row["name"] for row in category_pages if row.get("name"))
    for name in sorted(names, key=str.lower):
        print(name)


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
    sub.add_parser("list", help="List cached category names")

    p_find = sub.add_parser("find", help="Search cached category names")
    p_find.add_argument("text", help="Case-insensitive search text")

    p_check = sub.add_parser("check", help="Check one category name")
    p_check.add_argument("name", help="Category name, with or without Category: prefix")

    args = ap.parse_args()
    if not args.subcommand:
        ap.print_help()
        sys.exit(0)

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


if __name__ == "__main__":
    main()
