#!/usr/bin/env python3
# Copyright (c) 2026 Rob Lanphier and contributors
# SPDX-License-Identifier: MIT
# See LICENSE for details.
"""
rcmgr.py - cache and inspect target-wiki recent changes.

The cache belongs to the mwsync working directory:

  _cache/_recent_changes/manifest.json
  _cache/_recent_changes/changes/YYYY-MM-DD.jsonl
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys
import urllib.parse
import urllib.request

import mwsync

SCHEMA_VERSION = 1
OVERLAP_SECONDS = 10 * 60

RC_CACHE_DIR = os.path.join("_cache", "_recent_changes")
CHANGES_DIR = os.path.join(RC_CACHE_DIR, "changes")
MANIFEST_PATH = os.path.join(RC_CACHE_DIR, "manifest.json")
RC_PROPS = "ids|title|timestamp|user|userid|comment|flags|sizes|loginfo|tags|sha1"
RC_TYPES = "edit|new|log|categorize"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _format_timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object, *, label: str = "timestamp") -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    try:
        if value.endswith("Z"):
            parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            return parsed.replace(tzinfo=dt.timezone.utc)
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"invalid {label}: {value}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0)


def _parse_boundary(value: str | None, *, until: bool) -> dt.datetime | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        raise ValueError("empty timestamp boundary")
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            base = dt.datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
            return base + dt.timedelta(days=1) if until else base
        return _parse_timestamp(raw, label="timestamp boundary")
    except ValueError as e:
        raise ValueError(
            f"{e}; use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ"
        ) from None


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _record_sort_key(record: dict) -> tuple[str, int]:
    try:
        rcid = int(record.get("rcid"))
    except (TypeError, ValueError):
        rcid = -1
    return (str(record.get("timestamp", "")), rcid)


def _record_identity(record: dict) -> str:
    return str(record.get("rcid"))


def _partition_path(day: str) -> str:
    return os.path.join(CHANGES_DIR, f"{day}.jsonl")


def _change_day(record: dict) -> str:
    timestamp = record.get("timestamp")
    _parse_timestamp(timestamp, label="recent-change timestamp")
    return str(timestamp)[:10]


def _cache_missing() -> None:
    print("Recent-changes cache not found. Run: rcmgr.py fetch", file=sys.stderr)
    sys.exit(1)


def _read_json(path: str, *, required: bool) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        if required:
            _cache_missing()
        return None
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        print(f"Error: {path} must contain a JSON object", file=sys.stderr)
        sys.exit(1)
    return data


def _write_json(path: str, data: dict) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not mwsync._atomic_write(path, content):
        sys.exit(1)


def _read_jsonl(path: str, *, required: bool = False) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"Error: invalid JSON in {path}:{lineno}: {e}", file=sys.stderr)
                    sys.exit(1)
                if not isinstance(row, dict):
                    print(f"Error: {path}:{lineno} must contain a JSON object",
                          file=sys.stderr)
                    sys.exit(1)
                rows.append(row)
    except FileNotFoundError:
        if required:
            _cache_missing()
    return rows


def _write_jsonl(path: str, rows: list[dict]) -> None:
    content = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    )
    if not mwsync._atomic_write(path, content):
        sys.exit(1)


def _load_manifest(*, required: bool) -> dict | None:
    manifest = _read_json(MANIFEST_PATH, required=required)
    if manifest is None:
        return None
    schema = manifest.get("schema_version")
    if schema != SCHEMA_VERSION:
        print(
            f"Error: unsupported recent-changes cache schema_version {schema!r}; "
            f"expected {SCHEMA_VERSION}",
            file=sys.stderr,
        )
        sys.exit(1)
    return manifest


def _manifest_watermark(manifest: dict | None) -> dict | None:
    if not manifest:
        return None
    watermark = manifest.get("watermark")
    if watermark is None:
        return None
    if not isinstance(watermark, dict):
        print("Error: manifest watermark must be an object", file=sys.stderr)
        sys.exit(1)
    if "timestamp" not in watermark or "rcid" not in watermark:
        print("Error: manifest watermark must contain timestamp and rcid",
              file=sys.stderr)
        sys.exit(1)
    try:
        _parse_timestamp(watermark["timestamp"], label="manifest watermark timestamp")
        int(watermark["rcid"])
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    return watermark


def _fetch_start_from_watermark(watermark: dict | None) -> str | None:
    if not watermark:
        return None
    start = _parse_timestamp(
        watermark["timestamp"],
        label="manifest watermark timestamp",
    ) - dt.timedelta(seconds=OVERLAP_SECONDS)
    return _format_timestamp(start)


def _check_fetch_api_base(manifest: dict | None, api_base: str) -> None:
    if manifest is None:
        return
    cached = manifest.get("api_base")
    if cached and cached != api_base:
        print(
            "Error: recent-changes cache was built for a different wiki.",
            file=sys.stderr,
        )
        print(f"  cache api_base:  {cached}", file=sys.stderr)
        print(f"  config api_base: {api_base}", file=sys.stderr)
        print("Use a separate working directory or move _cache/_recent_changes aside.",
              file=sys.stderr)
        sys.exit(1)


def _api_get(api_base: str, params: dict) -> dict:
    url = api_base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": mwsync.USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("MediaWiki API returned non-object JSON")
    if "error" in data:
        error = data["error"]
        code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
        info = error.get("info", "unknown API error") if isinstance(error, dict) else error
        raise ValueError(f"MediaWiki API error ({code}): {info}")
    return data


def _fetch_recent_changes(api_base: str, start: str | None) -> list[dict]:
    rows: list[dict] = []
    continuation: dict = {}
    while True:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "list": "recentchanges",
            "rcdir": "newer",
            "rclimit": "max",
            "rcprop": RC_PROPS,
            "rctype": RC_TYPES,
        }
        if start:
            params["rcstart"] = start
        params.update(continuation)
        data = _api_get(api_base, params)
        batch = data.get("query", {}).get("recentchanges", [])
        if not isinstance(batch, list):
            raise ValueError("MediaWiki API response missing query.recentchanges list")
        for row in batch:
            if not isinstance(row, dict):
                raise ValueError("MediaWiki API returned a non-object recent-change row")
            rows.append(row)
        continuation = data.get("continue") or {}
        if not continuation:
            break
    return rows


def _validate_record(record: dict) -> None:
    for field in ("rcid", "timestamp", "type"):
        if field not in record:
            raise ValueError(f"recent-change row missing {field!r}: {record!r}")
    try:
        int(record["rcid"])
    except (TypeError, ValueError):
        raise ValueError(f"recent-change row has invalid rcid: {record!r}") from None
    _parse_timestamp(record["timestamp"], label="recent-change timestamp")
    if not isinstance(record["type"], str) or not record["type"]:
        raise ValueError(f"recent-change row has invalid type: {record!r}")


def _partition_paths() -> list[str]:
    return sorted(glob.glob(os.path.join(CHANGES_DIR, "*.jsonl")))


def _all_cached_records(staged: dict[str, list[dict]] | None = None) -> list[dict]:
    rows: list[dict] = []
    staged = staged or {}
    staged_paths = {_partition_path(day) for day in staged}
    for path in _partition_paths():
        if path in staged_paths:
            continue
        rows.extend(_read_jsonl(path))
    for day in sorted(staged):
        rows.extend(staged[day])
    return rows


def _compute_watermark(records: list[dict]) -> dict | None:
    if not records:
        return None
    newest = max(records, key=_record_sort_key)
    return {
        "timestamp": newest["timestamp"],
        "rcid": newest["rcid"],
    }


def _group_new_records(records: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for record in records:
        grouped.setdefault(_change_day(record), []).append(record)
    return grouped


def _merge_partitions(new_records: list[dict]) -> tuple[dict[str, list[dict]], int]:
    staged: dict[str, list[dict]] = {}
    newly_added = 0
    for day, rows in _group_new_records(new_records).items():
        existing = _read_jsonl(_partition_path(day))
        by_rcid = {_record_identity(row): row for row in existing}
        for row in rows:
            rcid = _record_identity(row)
            if rcid in by_rcid:
                continue
            by_rcid[rcid] = row
            newly_added += 1
        staged[day] = sorted(by_rcid.values(), key=_record_sort_key)
    return staged, newly_added


def _write_partitions(staged: dict[str, list[dict]]) -> None:
    for day in sorted(staged):
        _write_jsonl(_partition_path(day), staged[day])


def _load_log_records() -> list[dict]:
    manifest = _load_manifest(required=True)
    if not os.path.isdir(CHANGES_DIR) and (manifest or {}).get("total_changes", 0):
        print(f"Error: recent-changes partition directory missing: {CHANGES_DIR}",
              file=sys.stderr)
        sys.exit(1)
    return _all_cached_records()


def _status_age(timestamp: object) -> str:
    if not isinstance(timestamp, str):
        return ""
    try:
        fetched = _parse_timestamp(timestamp, label="last_fetch_at")
    except ValueError:
        return ""
    today = _utcnow().date()
    fetched_day = fetched.date()
    delta = (today - fetched_day).days
    if delta == 0:
        return " (today)"
    if delta == 1:
        return " (1 day ago)"
    if delta > 1:
        return f" ({delta} days ago)"
    return ""


def _normalize_title(value: str) -> str:
    return " ".join(value.replace("_", " ").strip().split()).casefold()


def _article_title_filter(config: dict, raw: str | None) -> str | None:
    if not raw:
        return None
    articles = config.get("wiki", {}).get("articles", {})
    if isinstance(articles, dict):
        entry = articles.get(raw)
        if isinstance(entry, dict) and entry.get("title"):
            return str(entry["title"])
        for key, candidate in articles.items():
            if not isinstance(candidate, dict):
                continue
            if raw == key or raw == candidate.get("local"):
                return str(candidate.get("title") or key)
    return raw.replace("_", " ").strip()


def _record_matches(record: dict,
                    *,
                    since: dt.datetime | None,
                    until: dt.datetime | None,
                    namespace: int | None,
                    change_type: str | None,
                    user: str | None,
                    title: str | None) -> bool:
    try:
        timestamp = _parse_timestamp(record.get("timestamp"), label="cached timestamp")
    except ValueError as e:
        print(f"Error: {e} in cached rcid {record.get('rcid')!r}", file=sys.stderr)
        sys.exit(1)
    if since is not None and timestamp < since:
        return False
    if until is not None and timestamp >= until:
        return False
    if namespace is not None:
        try:
            if int(record.get("ns")) != namespace:
                return False
        except (TypeError, ValueError):
            return False
    if change_type is not None and record.get("type") != change_type:
        return False
    if user is not None and record.get("user") != user:
        return False
    if title is not None and _normalize_title(str(record.get("title", ""))) != title:
        return False
    return True


def _comment_for_record(record: dict) -> str:
    comment = record.get("comment")
    if comment:
        return str(comment)
    logaction = record.get("logaction")
    if logaction:
        return str(logaction)
    return ""


def _format_log_records(records: list[dict]) -> list[str]:
    type_width = max([4] + [len(str(row.get("type", ""))) for row in records])
    title_width = max([5] + [len(str(row.get("title", ""))) for row in records])
    lines = []
    for row in records:
        timestamp = str(row.get("timestamp", ""))
        change_type = str(row.get("type", ""))
        title = str(row.get("title", ""))
        user = str(row.get("user", ""))
        comment = _comment_for_record(row)
        lines.append(
            f"{timestamp}  {change_type:<{type_width}}  "
            f"{title:<{title_width}}  {user}  {comment}"
        )
    return lines


def _manifest_for_empty_fetch(existing: dict | None, api_base: str, fetched_at: str) -> dict:
    if existing:
        manifest = dict(existing)
        manifest.setdefault("first_fetch_at", fetched_at)
        manifest["last_fetch_at"] = fetched_at
        return manifest
    return {
        "schema_version": SCHEMA_VERSION,
        "api_base": api_base,
        "first_fetch_at": fetched_at,
        "last_fetch_at": fetched_at,
        "watermark": None,
        "total_changes": 0,
    }


def run_fetch(args, config: dict) -> None:
    api_base = mwsync.get_api_base(config)
    manifest = _load_manifest(required=False)
    _check_fetch_api_base(manifest, api_base)
    watermark = _manifest_watermark(manifest)
    start = _fetch_start_from_watermark(watermark)

    if args.dry_run:
        print(f"Recent-changes fetch dry run for {api_base}")
        if watermark:
            print(f"  watermark: {watermark['timestamp']} (rcid {watermark['rcid']})")
            print(f"  rcstart:   {start} (10-minute overlap)")
        else:
            print("  watermark: none")
            print("  rcstart:   omitted (first run)")
        print("  writes:    no")
        return

    try:
        print(f"# Fetching recent changes from {api_base}...", file=sys.stderr)
        rows = _fetch_recent_changes(api_base, start)
        for row in rows:
            _validate_record(row)
        staged, added_count = _merge_partitions(rows)
        now = _format_timestamp(_utcnow())
        if not rows:
            _write_json(MANIFEST_PATH, _manifest_for_empty_fetch(manifest, api_base, now))
            print("# No recent changes returned", file=sys.stderr)
            return

        all_records = _all_cached_records(staged)
        new_watermark = _compute_watermark(all_records)
        new_manifest = {
            "schema_version": SCHEMA_VERSION,
            "api_base": api_base,
            "first_fetch_at": (manifest or {}).get("first_fetch_at") or now,
            "last_fetch_at": now,
            "watermark": new_watermark,
            "total_changes": len({_record_identity(row) for row in all_records}),
        }
        _write_partitions(staged)
        _write_json(MANIFEST_PATH, new_manifest)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"# Fetched {len(rows)} recent-change rows ({added_count} new)", file=sys.stderr)
    print(f"# Wrote {MANIFEST_PATH}", file=sys.stderr)


def run_status(args, config: dict) -> None:
    manifest = _load_manifest(required=True)
    api_base = manifest.get("api_base", "")
    watermark = _manifest_watermark(manifest)

    print(f"Recent-changes cache for {api_base}")
    print(f"  first fetch:  {manifest.get('first_fetch_at', '')}")
    last_fetch = manifest.get("last_fetch_at", "")
    print(f"  last fetch:   {last_fetch}{_status_age(last_fetch)}")
    if watermark:
        print(f"  watermark:    {watermark['timestamp']} (rcid {watermark['rcid']})")
    else:
        print("  watermark:    none")
    print(f"  changes:      {manifest.get('total_changes', 0)}")


def run_log(args, config: dict) -> None:
    try:
        since = _parse_boundary(args.since, until=False)
        until = _parse_boundary(args.until, until=True)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if since is not None and until is not None and until <= since:
        print("Error: --until must be later than --since", file=sys.stderr)
        sys.exit(1)

    title = _article_title_filter(config, args.article)
    title_norm = _normalize_title(title) if title else None
    rows = []
    for record in _load_log_records():
        if _record_matches(
            record,
            since=since,
            until=until,
            namespace=args.namespace,
            change_type=args.change_type,
            user=args.user,
            title=title_norm,
        ):
            rows.append(record)
    rows.sort(key=_record_sort_key, reverse=True)
    if args.limit is not None:
        rows = rows[:args.limit]
    for line in _format_log_records(rows):
        print(line)


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="rcmgr.py",
        description="Cache and inspect target-wiki recent changes.",
    )
    ap.add_argument(
        "--config", default=mwsync.DEFAULT_CONFIG_PATH,
        help=f"Path to config file (default: {mwsync.DEFAULT_CONFIG_PATH})",
    )
    sub = ap.add_subparsers(dest="subcommand", help="Available subcommands")

    p_fetch = sub.add_parser("fetch", help="Refresh _cache/_recent_changes")
    p_fetch.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the planned query window without fetching or writing",
    )

    sub.add_parser("status", help="Show recent-changes cache status")

    p_log = sub.add_parser("log", help="Print cached recent changes")
    p_log.add_argument("article", nargs="?", help="Article key or title to filter")
    p_log.add_argument("--since", help="Include changes at or after this UTC date/time")
    p_log.add_argument("--until", help="Include changes before this UTC date/time")
    p_log.add_argument("--ns", dest="namespace", type=int, help="Filter by namespace ID")
    p_log.add_argument("--type", dest="change_type", help="Filter by change type")
    p_log.add_argument("--user", help="Filter by editor username")
    p_log.add_argument("--limit", type=_positive_int, help="Maximum rows to print")

    args = ap.parse_args()
    if not args.subcommand:
        ap.print_help()
        sys.exit(0)

    config = mwsync.load_config(args.config)
    if args.subcommand == "fetch":
        run_fetch(args, config)
    elif args.subcommand == "status":
        run_status(args, config)
    elif args.subcommand == "log":
        run_log(args, config)


if __name__ == "__main__":
    main()
