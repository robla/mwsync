#!/usr/bin/env python3
"""Prototype mwmap command-line interface."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from urllib import error, parse, request

import yaml


CONFIG_DIR = "_mwmap"
CONFIG_PATH = Path(CONFIG_DIR) / "config.yaml"
CACHE_DIR = Path(CONFIG_DIR) / "cache"
USER_AGENT = "mwmap/0.1 prototype"


def _die(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _config_path(root: Path) -> Path:
    return root / CONFIG_PATH


def _cache_dir(root: Path) -> Path:
    return root / CACHE_DIR


def _initial_config() -> dict[str, Any]:
    return {"version": 1, "remotes": {}, "mappings": []}


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _save_config(root: Path, config: dict[str, Any]) -> None:
    text = yaml.safe_dump(config, sort_keys=False)
    _atomic_write_text(_config_path(root), text)


def _load_config(root: Path) -> dict[str, Any]:
    path = _config_path(root)
    if not path.exists():
        _die(f"config file not found: {path}. Run: mwmap.py init")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _die(f"could not parse config file {path}: {exc}")
    if not isinstance(data, dict):
        _die(f"config file is not a YAML mapping: {path}")
    data.setdefault("version", 1)
    data.setdefault("remotes", {})
    data.setdefault("mappings", [])
    return data


def _init_workspace(root: Path) -> bool:
    root.mkdir(parents=True, exist_ok=True)
    _cache_dir(root).mkdir(parents=True, exist_ok=True)
    path = _config_path(root)
    if path.exists():
        return False
    _save_config(root, _initial_config())
    return True


def _remote_name_from_host(hostname: str) -> str:
    if hostname.startswith("www."):
        hostname = hostname[4:]
    name = hostname.split(".", 1)[0]
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "remote"


def _unique_remote_name(config: dict[str, Any], preferred: str, location: str) -> str:
    remotes = config.setdefault("remotes", {})
    existing = remotes.get(preferred)
    if existing is None or existing.get("location") == location:
        return preferred

    suffix = 2
    while True:
        candidate = f"{preferred}-{suffix}"
        existing = remotes.get(candidate)
        if existing is None or existing.get("location") == location:
            return candidate
        suffix += 1


def _mediawiki_api_from_page_url(url: str) -> tuple[str, str, str]:
    parsed = parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _die(f"clone expects an absolute http(s) MediaWiki page URL: {url}")

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
        _die("clone currently supports MediaWiki page URLs like https://host/wiki/Page")

    if not title:
        _die(f"could not determine page title from URL: {url}")

    api_url = parse.urlunparse((parsed.scheme, parsed.netloc, api_path, "", "", ""))
    remote_location = parse.urlunparse(
        (parsed.scheme, parsed.netloc, api_path.rsplit("/api.php", 1)[0] + "/", "", "", "")
    )
    return title, api_url, remote_location


def _fetch_mediawiki_page(api_url: str, title: str) -> tuple[str, dict[str, Any]]:
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
        _die(f"failed to fetch page from MediaWiki: {exc}")
    except json.JSONDecodeError as exc:
        _die(f"MediaWiki returned invalid JSON: {exc}")

    pages = payload.get("query", {}).get("pages", [])
    if not pages:
        _die(f"MediaWiki returned no page for title: {title}")
    page = pages[0]
    if page.get("missing"):
        _die(f"MediaWiki page does not exist: {title}")

    revisions = page.get("revisions") or []
    if not revisions:
        _die(f"MediaWiki returned no revisions for title: {title}")
    revision = revisions[0]
    slots = revision.get("slots") or {}
    main_slot = slots.get("main") or {}
    content = main_slot.get("content", revision.get("*"))
    if content is None:
        _die(f"MediaWiki response did not include page content for: {title}")

    metadata = {
        "pageid": page.get("pageid"),
        "title": page.get("title", title),
        "revid": revision.get("revid"),
        "parentid": revision.get("parentid"),
        "timestamp": revision.get("timestamp"),
    }
    return content, metadata


def _local_path_for_title(title: str) -> Path:
    name = title.strip().replace(" ", "_")
    name = name.replace("/", "__")
    name = re.sub(r"[^A-Za-z0-9._:-]+", "_", name)
    return Path(f"{name or 'page'}.mw")


def _cache_page(root: Path, remote: str, title: str, content: str, metadata: dict[str, Any]) -> None:
    revid = metadata.get("revid")
    if revid is None:
        _die(f"cannot cache page without a MediaWiki revision id: {title}")

    revid_text = str(revid)
    page_key = _local_path_for_title(title).with_suffix("").name
    page_dir = _cache_dir(root) / remote / page_key
    body_name = f"{revid_text}.mw"
    _atomic_write_text(
        page_dir / body_name,
        content,
    )
    _atomic_write_text(page_dir / f"{revid_text}.yaml", yaml.safe_dump(metadata, sort_keys=False))
    _write_history(page_dir, {**metadata, "body": body_name})


def _write_history(page_dir: Path, fetched: dict[str, Any]) -> None:
    history_path = page_dir / "history.jsonl"
    records_by_revid: dict[str, dict[str, Any]] = {}
    if history_path.exists():
        for line_number, line in enumerate(history_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                _die(f"could not parse {history_path}:{line_number}: {exc}")
            if "revid" in record:
                records_by_revid[str(record["revid"])] = record

    records_by_revid[str(fetched["revid"])] = fetched
    records = sorted(
        records_by_revid.values(),
        key=lambda record: (record.get("timestamp") or "", str(record.get("revid") or "")),
    )
    text = "".join(f"{json.dumps(record, sort_keys=True)}\n" for record in records)
    _atomic_write_text(history_path, text)


def _mapping_exists(config: dict[str, Any], remote: str, title: str, local_path: str) -> bool:
    for mapping in config.setdefault("mappings", []):
        if (
            mapping.get("remote") == remote
            and mapping.get("remote_path") == title
            and mapping.get("local_path") == local_path
        ):
            return True
    return False


def run_init(args: argparse.Namespace) -> int:
    created = _init_workspace(args.root)
    if created:
        print(f"Initialized mwmap workspace in {args.root / CONFIG_DIR}")
    else:
        print(f"mwmap workspace already initialized in {args.root / CONFIG_DIR}")
    return 0


def run_remote_add(args: argparse.Namespace) -> int:
    config = _load_config(args.root)
    remotes = config.setdefault("remotes", {})
    if args.name in remotes:
        _die(f"remote already exists: {args.name}")
    remotes[args.name] = {"type": args.type, "location": args.location}
    _save_config(args.root, config)
    print(f"Added remote {args.name}")
    return 0


def run_status(args: argparse.Namespace) -> int:
    config = _load_config(args.root)
    remotes = config.get("remotes") or {}
    mappings = config.get("mappings") or []

    print(f"{len(remotes)} remotes")
    for name, remote in remotes.items():
        print(f"  {name} ({remote.get('type', 'unknown')}): {remote.get('location', '')}")
    print(f"{len(mappings)} mappings")
    return 0


def run_clone(args: argparse.Namespace) -> int:
    _init_workspace(args.root)
    config = _load_config(args.root)

    title, api_url, remote_location = _mediawiki_api_from_page_url(args.url)
    preferred_name = _remote_name_from_host(parse.urlparse(args.url).hostname or "remote")
    remote_name = _unique_remote_name(config, preferred_name, remote_location)

    remotes = config.setdefault("remotes", {})
    remotes.setdefault(
        remote_name,
        {"type": "mediawiki", "location": remote_location},
    )

    content, metadata = _fetch_mediawiki_page(api_url, title)
    local_path = Path(args.path) if args.path else _local_path_for_title(metadata["title"])
    target = args.root / local_path
    if target.exists():
        _die(f"local path already exists: {target}")
    _atomic_write_text(target, content)
    _cache_page(args.root, remote_name, metadata["title"], content, metadata)

    local_path_text = local_path.as_posix()
    if not _mapping_exists(config, remote_name, metadata["title"], local_path_text):
        config.setdefault("mappings", []).append(
            {
                "type": "page",
                "remote": remote_name,
                "remote_path": metadata["title"],
                "local_path": local_path_text,
            }
        )
    _save_config(args.root, config)

    print(f"Cloned {metadata['title']} to {local_path_text}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mwmap.py")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Workspace root containing _mwmap/ (default: current directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize an mwmap workspace")
    p_init.set_defaults(func=run_init)

    p_clone = sub.add_parser("clone", help="Onboard a MediaWiki page URL")
    p_clone.add_argument("url", help="MediaWiki page URL")
    p_clone.add_argument("path", nargs="?", help="Optional local output path")
    p_clone.set_defaults(func=run_clone)

    p_remote = sub.add_parser("remote", help="Manage remotes")
    remote_sub = p_remote.add_subparsers(dest="remote_command", required=True)
    p_remote_add = remote_sub.add_parser("add", help="Add a remote")
    p_remote_add.add_argument("name")
    p_remote_add.add_argument("type")
    p_remote_add.add_argument("location")
    p_remote_add.set_defaults(func=run_remote_add)

    p_status = sub.add_parser("status", help="Show workspace status")
    p_status.set_defaults(func=run_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
