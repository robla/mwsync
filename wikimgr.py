#!/usr/bin/env python3
# Copyright (c) 2026 Rob Lanphier and contributors
# SPDX-License-Identifier: MIT
# See LICENSE for details.
"""
wikimgr.py - cache and list target-wiki titles by namespace.

The cache belongs to the mwsync working directory:

  _cache/_titles/manifest.json
  _cache/_titles/titles_ns_00.jsonl
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import sys
import urllib.parse
import urllib.request

import mwsync

TITLE_CACHE_DIR = os.path.join("_cache", "_titles")
MANIFEST_PATH = os.path.join(TITLE_CACHE_DIR, "manifest.json")
DEFAULT_NAMESPACE = 0

try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass


def _namespace_file(namespace: int) -> str:
    return f"titles_ns_{namespace:02d}.jsonl"


def _namespace_path(namespace: int) -> str:
    return os.path.join(TITLE_CACHE_DIR, _namespace_file(namespace))


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


def _fetch_namespace_titles(api_base: str, namespace: int) -> list[dict]:
    rows = []
    continuation = {}
    while True:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "list": "allpages",
            "apnamespace": str(namespace),
            "aplimit": "max",
            "apfilterredir": "all",
        }
        params.update(continuation)
        data = _api_get(api_base, params)
        for row in data.get("query", {}).get("allpages", []):
            title = row.get("title", "")
            if not isinstance(title, str) or not title:
                continue
            rows.append({
                "namespace": int(row.get("ns", namespace)),
                "title": title,
                "pageid": int(row.get("pageid") or 0),
                "redirect": bool(row.get("redirect")),
            })
        continuation = data.get("continue")
        if not continuation:
            break
    return sorted(rows, key=lambda item: item["title"].lower())


def _write_json(path: str, data: dict) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not mwsync._atomic_write(path, content):
        sys.exit(1)


def _write_jsonl(path: str, rows: list[dict]) -> None:
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                      for row in rows)
    if not mwsync._atomic_write(path, content):
        sys.exit(1)


def _read_manifest() -> dict:
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"Warning: could not read {MANIFEST_PATH}: {e}", file=sys.stderr)
        return {}
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
                    print(f"Error: invalid JSON in {path}:{lineno}: {e}",
                          file=sys.stderr)
                    sys.exit(1)
                if isinstance(item, dict):
                    rows.append(item)
    except FileNotFoundError:
        print(f"Title cache for namespace not found: {path}", file=sys.stderr)
        print("Run: wikimgr.py fetch --namespace N", file=sys.stderr)
        sys.exit(1)
    return rows


def _normalize_namespaces(values: list[int] | None) -> list[int]:
    namespaces = values or [DEFAULT_NAMESPACE]
    out = []
    seen = set()
    for namespace in namespaces:
        if namespace < 0:
            print(f"Error: namespace must be non-negative: {namespace}",
                  file=sys.stderr)
            sys.exit(1)
        if namespace not in seen:
            seen.add(namespace)
            out.append(namespace)
    return out


def run_fetch(args, config: dict) -> None:
    api_base = mwsync.get_api_base(config)
    namespaces = _normalize_namespaces(args.namespace)
    manifest = _read_manifest()
    manifest.setdefault("namespaces", {})
    manifest["api_base"] = api_base
    manifest["fetched_at"] = dt.datetime.now(
        dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        for namespace in namespaces:
            print(f"# Fetching namespace {namespace} titles from {api_base}...",
                  file=sys.stderr)
            rows = _fetch_namespace_titles(api_base, namespace)
            path = _namespace_path(namespace)
            _write_jsonl(path, rows)
            manifest["namespaces"][str(namespace)] = {
                "file": _namespace_file(namespace),
                "titles_count": len(rows),
            }
            print(f"# Wrote {path} ({len(rows)} titles)", file=sys.stderr)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    manifest["titles_count"] = sum(
        int(item.get("titles_count") or 0)
        for item in manifest.get("namespaces", {}).values()
        if isinstance(item, dict)
    )
    _write_json(MANIFEST_PATH, manifest)
    print(f"# Wrote {MANIFEST_PATH}", file=sys.stderr)


def run_list(args, config: dict) -> None:
    _api_base = mwsync.get_api_base(config)
    namespace = args.namespace
    if namespace < 0:
        print(f"Error: namespace must be non-negative: {namespace}",
              file=sys.stderr)
        sys.exit(1)
    rows = _read_jsonl(_namespace_path(namespace))
    for row in rows:
        title = row.get("title")
        if isinstance(title, str) and title:
            print(title)


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="wikimgr.py",
        description="Cache and list target-wiki titles by namespace.",
    )
    ap.add_argument(
        "--config", default=mwsync.DEFAULT_CONFIG_PATH,
        help=f"Path to config file (default: {mwsync.DEFAULT_CONFIG_PATH})",
    )
    sub = ap.add_subparsers(dest="subcommand", help="Available subcommands")

    p_fetch = sub.add_parser("fetch", help="Refresh _cache/_titles")
    p_fetch.add_argument(
        "--namespace", type=int, action="append",
        help=("Namespace ID to fetch. Repeat for multiple namespaces "
              f"(default: {DEFAULT_NAMESPACE})."),
    )

    p_list = sub.add_parser("list", help="List cached titles in a namespace")
    p_list.add_argument(
        "--namespace", type=int, default=DEFAULT_NAMESPACE,
        help=f"Namespace ID to list (default: {DEFAULT_NAMESPACE}).",
    )

    args = ap.parse_args()
    if not args.subcommand:
        ap.print_help()
        sys.exit(0)

    config = mwsync.load_config(args.config)

    if args.subcommand == "fetch":
        run_fetch(args, config)
    elif args.subcommand == "list":
        run_list(args, config)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # Common for commands like `wikimgr.py list | head`.
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        sys.exit(0)
