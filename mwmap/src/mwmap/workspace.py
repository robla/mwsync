"""Workspace, config, and cache helpers.

Typical command flow:
  command handler -> load/save workspace config here -> atomic writes in core.misc
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

import yaml

from mwmap.core.misc import atomic_write_text, die


CONFIG_DIR = "_mwmap"
CONFIG_FILENAME = "mwmap.yaml"
LEGACY_CONFIG_FILENAME = "config.yaml"
CONFIG_PATH = Path(CONFIG_DIR) / CONFIG_FILENAME
LEGACY_CONFIG_PATH = Path(CONFIG_DIR) / LEGACY_CONFIG_FILENAME
CACHE_DIR = Path(CONFIG_DIR) / "cache"


def config_path(root: Path) -> Path:
    """Return the workspace config path under `root`."""
    return root / CONFIG_PATH


def legacy_config_path(root: Path) -> Path:
    """Return the pre-rename workspace config path under `root`."""
    return root / LEGACY_CONFIG_PATH


def cache_dir(root: Path) -> Path:
    """Return the disposable workspace cache directory under `root`."""
    return root / CACHE_DIR


def remote_cache_dir(root: Path, remote: str) -> Path:
    """Return `cache/<remote>/`, the root for one remote's cache."""
    return cache_dir(root) / remote


def pages_dir(root: Path, remote: str) -> Path:
    """Return `cache/<remote>/pages/`, the canonical page cache root."""
    return remote_cache_dir(root, remote) / "pages"


def page_cache_dir(root: Path, remote: str, pageid: Any) -> Path:
    """Return the canonical cache directory for one remote pageid."""
    return pages_dir(root, remote) / str(pageid)


def by_title_dir(root: Path, remote: str) -> Path:
    """Return `cache/<remote>/by-title/`, the readable alias index root."""
    return remote_cache_dir(root, remote) / "by-title"


def initial_config() -> dict[str, Any]:
    """Return the v1 empty workspace config."""
    return {"version": 1, "remotes": {}, "mappings": []}


def save_workspace_config(root: Path, config: dict[str, Any]) -> None:
    """Atomically write `_mwmap/mwmap.yaml`."""
    text = yaml.safe_dump(config, sort_keys=False)
    atomic_write_text(config_path(root), text)


def load_workspace_config(root: Path) -> dict[str, Any]:
    """Load config, filling default top-level keys used by v1."""
    path = config_path(root)
    if not path.exists() and legacy_config_path(root).exists():
        path = legacy_config_path(root)
    if not path.exists():
        die(f"config file not found: {config_path(root)}. Run: mwmap.py init")
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
    if path.exists() or legacy_config_path(root).exists():
        return False
    save_workspace_config(root, initial_config())
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


def register_remote(
    config: dict[str, Any], preferred: str, remote_type: str, location: str
) -> str:
    """Ensure a remote for `location` exists; return its (possibly suffixed) name.

    Used by `clone` to auto-register a remote derived from a URL. The explicit
    `remote add` verb has stricter, error-on-duplicate behavior of its own.
    """
    name = unique_remote_name(config, preferred, location)
    config.setdefault("remotes", {}).setdefault(
        name, {"type": remote_type, "location": location}
    )
    return name


def cache_page_rev(root: Path, remote: str, content: str, metadata: dict[str, Any]) -> None:
    """Cache one fetched MediaWiki revision under cache/<remote>/pages/<pageid>/.

    The cache directory is keyed on the stable MediaWiki `pageid`, not the
    movable title, so a page move on the wiki does not orphan its history.
    A `page.yaml` marker keeps the numeric directory self-describing.
    """
    title = metadata.get("title")
    pageid = metadata.get("pageid")
    if pageid is None:
        die(f"cannot cache page without a MediaWiki page id: {title}")
    revid = metadata.get("revid")
    if revid is None:
        die(f"cannot cache page without a MediaWiki revision id: {title}")

    revid_text = str(revid)
    page_dir = page_cache_dir(root, remote, pageid)
    body_name = f"{revid_text}.mw"
    atomic_write_text(page_dir / body_name, content)
    atomic_write_text(page_dir / f"{revid_text}.yaml", yaml.safe_dump(metadata, sort_keys=False))
    title_key = title_key_for_title(str(title or pageid))
    ns = metadata.get("namespace", 0)
    ns_name = metadata.get("namespace_name") or ("main" if ns == 0 else str(ns))

    # Caching a revision records `current_revid` (the newest fetched) but must
    # never silently advance `base_revid` (what the working file derives from):
    # only the first cache (clone) and an explicit merge set the base.
    existing = load_page_info(root, remote, pageid)
    if existing and existing.get("base_revid") is not None:
        base_revid = existing["base_revid"]
    else:
        base_revid = metadata.get("base_revid", revid)
    write_page_info(
        page_dir,
        {
            "pageid": pageid,
            "remote": remote,
            "namespace": ns,
            "namespace_name": ns_name,
            "title": title,
            "title_key": title_key,
            "current_revid": revid,
            "base_revid": base_revid,
        },
    )
    write_hist_entry(page_dir, {**metadata, "body": body_name})
    # The readable <title-key>.mw alias points at the base body (the working
    # file's current base revision), not necessarily the newest fetched one.
    write_page_aliases(root, remote, pageid, title_key, ns, ns_name, f"{base_revid}.mw")


def write_page_info(page_dir: Path, info: dict[str, Any]) -> None:
    """Write the readable cache/<remote>/pages/<pageid>/page.yaml marker.

    Because cache directories are named by numeric pageid, this records the
    current title (and remote) so the directory is identifiable at a glance.
    """
    atomic_write_text(page_dir / "page.yaml", yaml.safe_dump(info, sort_keys=False))


def title_key_for_title(title: str) -> str:
    """Return a readable mwsync-style key for a MediaWiki title."""
    key = title.strip().replace(" ", "_")
    key = key.replace(":", "__").replace("/", "__")
    key = re.sub(r"[^A-Za-z0-9._'-]+", "_", key)
    return key or "page"


def namespace_dir_name(namespace: Any, namespace_name: str | None) -> str:
    """Return a readable namespace directory name like `14ns_Category`."""
    try:
        ns_num = int(namespace)
    except (TypeError, ValueError):
        ns_num = 0
    label = namespace_name or ("main" if ns_num == 0 else str(ns_num))
    label = title_key_for_title(label)
    if ns_num == 0 and label in {"", "0"}:
        label = "main"
    return f"{ns_num:02d}ns_{label}"


def _encode_dbkey_segment(value: str) -> str:
    """Normalize whitespace and escape characters unsafe in a filename.

    Mirrors legacy mwsync's `_encode_dbkey_segment` (spaces->`_`, `/`->`__` for
    subpages), and additionally escapes any residual `:` (mwsync leaves these in
    main-namespace titles, but a literal colon breaks rsync/scp `host:path`
    parsing, Windows filenames, and Zim's `:` page separator).
    """
    normalized = "_".join(value.replace("_", " ").split())
    return normalized.replace("/", "__").replace(":", "__")


def page_dbkey(title: str, namespace: Any, namespace_name: str | None = None) -> str:
    """Return the namespace-stripped, filename-safe page key for a title.

    e.g. ("User:RobLa", 2, "User") -> "RobLa"; ("California", 0) -> "California".
    """
    page_part = title
    if _as_int(namespace) != 0:
        prefix = f"{namespace_name}:" if namespace_name else None
        if prefix and title.startswith(prefix):
            page_part = title[len(prefix):]
        elif ":" in title:
            page_part = title.split(":", 1)[1]
    return _encode_dbkey_segment(page_part)


def local_path_for_page(
    title: str, namespace: Any = 0, namespace_name: str | None = None
) -> Path:
    """Derive the working-copy relative path for a page, mwsync-style.

    Main-namespace pages live at `<dbkey>.mw` under the root; pages in another
    namespace go under a `<NNns_Name>/` directory as `<dbkey>.mw`, with subpage
    slashes escaped to `__`. This keeps literal `:` and `/` out of working
    filenames. Mirrors legacy mwsync's `_local_for_title_parts`.
    """
    dbkey = page_dbkey(title, namespace, namespace_name) or "page"
    if _as_int(namespace) == 0:
        return Path(f"{dbkey}.mw")
    return Path(namespace_dir_name(namespace, namespace_name)) / f"{dbkey}.mw"


def _as_int(value: Any) -> int:
    """Best-effort int coercion for a namespace id (default 0)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def write_page_aliases(
    root: Path,
    remote: str,
    pageid: Any,
    title_key: str,
    namespace: Any,
    namespace_name: str | None,
    body_name: str,
) -> None:
    """Write rebuildable human-readable aliases for one cached page."""
    page_dir = page_cache_dir(root, remote, pageid)
    _symlink_or_copy(page_dir / body_name, page_dir / f"{title_key}.mw")

    ns_dir = by_title_dir(root, remote) / namespace_dir_name(namespace, namespace_name)
    alias_path = ns_dir / title_key
    rel_target = Path("..") / ".." / "pages" / str(pageid)
    _symlink_or_pointer(rel_target, alias_path, {"pageid": pageid, "target": str(rel_target)})


def _symlink_or_copy(target: Path, link: Path) -> None:
    """Create a symlink to a file, or copy the file if symlinks are unavailable."""
    _remove_alias(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target.name, link)
    except OSError:
        shutil.copyfile(target, link)


def _symlink_or_pointer(target: Path, link: Path, pointer: dict[str, Any]) -> None:
    """Create a symlink to a directory, or write a YAML pointer fallback."""
    _remove_alias(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        atomic_write_text(link, yaml.safe_dump(pointer, sort_keys=False))


def _remove_alias(path: Path) -> None:
    """Remove an existing symlink/file alias before recreating it."""
    if path.is_symlink() or path.is_file():
        path.unlink()


def site_info_path(root: Path, remote: str) -> Path:
    """Return the per-remote `cache/<remote>/site.yaml` path."""
    return cache_dir(root) / remote / "site.yaml"


def save_site_info(root: Path, remote: str, info: dict[str, Any]) -> None:
    """Cache one remote's siteinfo (server, paths, namespaces)."""
    atomic_write_text(site_info_path(root, remote), yaml.safe_dump(info, sort_keys=False))


def load_page_info(root: Path, remote: str, pageid: Any) -> dict[str, Any] | None:
    """Return the cached `page.yaml` for one pageid, or None if absent/invalid."""
    path = page_cache_dir(root, remote, pageid) / "page.yaml"
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def cached_body_path(root: Path, remote: str, pageid: Any, revid: Any) -> Path:
    """Return the cached body file for one remote revision."""
    return page_cache_dir(root, remote, pageid) / f"{revid}.mw"


def update_cache_base(root: Path, remote: str, pageid: Any, base_revid: Any) -> None:
    """Advance the cached base revision after a merge and re-point its alias.

    The durable source of truth for `base_revid` is the `mwmap.yaml` mapping;
    this keeps the disposable cache (`page.yaml` + the readable `<title>.mw`
    alias) consistent with it.
    """
    info = load_page_info(root, remote, pageid) or {}
    info["base_revid"] = base_revid
    page_dir = page_cache_dir(root, remote, pageid)
    write_page_info(page_dir, info)
    title_key = info.get("title_key")
    if title_key:
        _symlink_or_copy(page_dir / f"{base_revid}.mw", page_dir / f"{title_key}.mw")


def pending_commit_meta_path(root: Path, remote: str, pageid: Any) -> Path:
    """Return the pending-commit metadata path for one page (`commit.json`)."""
    return page_cache_dir(root, remote, pageid) / "commit.json"


def pending_commit_body_path(root: Path, remote: str, pageid: Any) -> Path:
    """Return the pending-commit body path for one page (`commit.mw`)."""
    return page_cache_dir(root, remote, pageid) / "commit.mw"


def preview_html_path(root: Path, remote: str, pageid: Any) -> Path:
    """Return the disposable preview HTML path for one page."""
    return page_cache_dir(root, remote, pageid) / "preview.html"


def read_pending_commit(root: Path, remote: str, pageid: Any) -> dict[str, Any] | None:
    """Return staged commit metadata for one page, or None if nothing staged.

    A pending commit is local intent (not repopulatable from the remote), but it
    is recoverable by re-`commit`-ing from the working file, so storing it in the
    otherwise-disposable cache degrades gracefully if the cache is wiped.
    """
    meta_path = pending_commit_meta_path(root, remote, pageid)
    if not meta_path.exists():
        return None
    if not pending_commit_body_path(root, remote, pageid).exists():
        die(f"pending commit metadata exists but body is missing: {meta_path}")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"could not parse pending commit {meta_path}: {exc}")
    return meta if isinstance(meta, dict) else None


def write_pending_commit(
    root: Path, remote: str, pageid: Any, meta: dict[str, Any], body: str
) -> None:
    """Stage a pending commit (body then metadata) for one page."""
    atomic_write_text(pending_commit_body_path(root, remote, pageid), body)
    atomic_write_text(
        pending_commit_meta_path(root, remote, pageid),
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
    )


def clear_pending_commit(root: Path, remote: str, pageid: Any) -> None:
    """Remove any staged commit for one page (after a successful push)."""
    for path in (
        pending_commit_meta_path(root, remote, pageid),
        pending_commit_body_path(root, remote, pageid),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def iter_page_mappings(
    config: dict[str, Any], local_path: str | None = None
) -> list[dict[str, Any]]:
    """Return page mappings, optionally limited to one working-tree path."""
    mappings = []
    for mapping in config.get("mappings") or []:
        if mapping.get("type") != "page":
            continue
        if local_path is not None and mapping.get("local_path") != local_path:
            continue
        mappings.append(mapping)
    return mappings


def write_hist_entry(page_dir: Path, fetched: dict[str, Any]) -> None:
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


def upsert_page_mapping(
    config: dict[str, Any],
    *,
    remote: str,
    pageid: int,
    title: str,
    local_path: str,
    fmt: str,
    base_revid: Any,
) -> dict[str, Any]:
    """Insert or update the page mapping identified by (remote, pageid).

    Identity is the stable pageid, not the movable title, so re-cloning a
    moved/renamed page updates the existing mapping (refreshing its title)
    instead of creating a duplicate. `base_revid` records which revision the
    working file was derived from — the merge-base for diff/merge/push.
    """
    record = {
        "type": "page",
        "remote": remote,
        "pageid": pageid,
        "format": fmt,
        "remote_path": title,
        "local_path": local_path,
        "base_revid": base_revid,
    }
    mappings = config.setdefault("mappings", [])
    for index, mapping in enumerate(mappings):
        if mapping.get("remote") == remote and mapping.get("pageid") == pageid:
            mappings[index] = record
            return record
    mappings.append(record)
    return record
