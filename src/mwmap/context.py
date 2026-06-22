"""Workspace, config, and cache helpers.

Typical command flow:
  command handler -> load/save config here -> atomic file writes in core.misc
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from mwmap.core.misc import atomic_write_text, die, local_path_for_title


CONFIG_DIR = "_mwmap"
CONFIG_PATH = Path(CONFIG_DIR) / "config.yaml"
CACHE_DIR = Path(CONFIG_DIR) / "cache"


def config_path(root: Path) -> Path:
    """Return the workspace config path under `root`."""
    return root / CONFIG_PATH


def cache_dir(root: Path) -> Path:
    """Return the disposable workspace cache directory under `root`."""
    return root / CACHE_DIR


def initial_config() -> dict[str, Any]:
    """Return the v1 empty workspace config."""
    return {"version": 1, "remotes": {}, "mappings": []}


def save_config(root: Path, config: dict[str, Any]) -> None:
    """Atomically write `_mwmap/config.yaml`."""
    text = yaml.safe_dump(config, sort_keys=False)
    atomic_write_text(config_path(root), text)


def load_config(root: Path) -> dict[str, Any]:
    """Load config, filling default top-level keys used by v1."""
    path = config_path(root)
    if not path.exists():
        die(f"config file not found: {path}. Run: mwmap.py init")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        die(f"could not parse config file {path}: {exc}")
    if not isinstance(data, dict):
        die(f"config file is not a YAML mapping: {path}")
    data.setdefault("version", 1)
    data.setdefault("remotes", {})
    data.setdefault("mappings", [])
    return data


def init_workspace(root: Path) -> bool:
    """Create workspace directories and return True if config was new."""
    root.mkdir(parents=True, exist_ok=True)
    cache_dir(root).mkdir(parents=True, exist_ok=True)
    path = config_path(root)
    if path.exists():
        return False
    save_config(root, initial_config())
    return True


def unique_remote_name(config: dict[str, Any], preferred: str, location: str) -> str:
    """Return a non-conflicting remote name for `location`."""
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


def cache_page(root: Path, remote: str, title: str, content: str, metadata: dict[str, Any]) -> None:
    """Cache one fetched MediaWiki revision under revid-stable filenames."""
    revid = metadata.get("revid")
    if revid is None:
        die(f"cannot cache page without a MediaWiki revision id: {title}")

    revid_text = str(revid)
    page_key = local_path_for_title(title).with_suffix("").name
    page_dir = cache_dir(root) / remote / page_key
    body_name = f"{revid_text}.mw"
    atomic_write_text(page_dir / body_name, content)
    atomic_write_text(page_dir / f"{revid_text}.yaml", yaml.safe_dump(metadata, sort_keys=False))
    write_history(page_dir, {**metadata, "body": body_name})


def write_history(page_dir: Path, fetched: dict[str, Any]) -> None:
    """Merge one revision record into a chronological `history.jsonl`."""
    history_path = page_dir / "history.jsonl"
    records_by_revid: dict[str, dict[str, Any]] = {}
    if history_path.exists():
        for line_number, line in enumerate(history_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                die(f"could not parse {history_path}:{line_number}: {exc}")
            if "revid" in record:
                records_by_revid[str(record["revid"])] = record

    records_by_revid[str(fetched["revid"])] = fetched
    records = sorted(
        records_by_revid.values(),
        key=lambda record: (record.get("timestamp") or "", str(record.get("revid") or "")),
    )
    text = "".join(f"{json.dumps(record, sort_keys=True)}\n" for record in records)
    atomic_write_text(history_path, text)


def mapping_exists(config: dict[str, Any], remote: str, title: str, local_path: str) -> bool:
    """Return whether config already has this exact page mapping."""
    for mapping in config.setdefault("mappings", []):
        if (
            mapping.get("remote") == remote
            and mapping.get("remote_path") == title
            and mapping.get("local_path") == local_path
        ):
            return True
    return False

