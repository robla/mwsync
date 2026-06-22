"""Implementation of `mwmap merge`.

Integrates the latest cached upstream revision into each paired working file,
three-way merging against the recorded base revision (`base_revid`). Uses only
the cache (mirrors `git merge`); run `fetch` first, or use `pull` to do both.

On a clean merge the working file is rewritten and `base_revid` advances to the
upstream revision. On conflict the file is written with conflict markers and
`base_revid` is left unchanged, so re-running after `fetch` stays meaningful.

Typical call stack:
  run_merge() -> merge_selected() -> _merge_one() per mapping
"""

from __future__ import annotations

import argparse
from collections import namedtuple

from mwmap.core.textmerge import three_way_merge
from mwmap.sync import write_local_body
from mwmap.workspace import (
    cached_body_path,
    iter_page_mappings,
    load_page_info,
    load_workspace_config,
    save_workspace_config,
    update_cache_base,
)

# base_advanced_to is the new base_revid on success, or None to leave it as-is.
_MergeResult = namedtuple("_MergeResult", "message conflicted base_advanced_to")


def run_merge(args: argparse.Namespace) -> int:
    """Merge cached upstream into all paired working files (or one path)."""
    config = load_workspace_config(args.root)
    mappings = iter_page_mappings(config, getattr(args, "path", None))
    if not mappings:
        print("no page mappings to merge")
        return 0
    return merge_selected(args.root, config, mappings)


def merge_selected(root, config, mappings) -> int:
    """Merge the given mappings, save advanced bases, return conflict count."""
    conflicts = 0
    changed = False
    for mapping in mappings:
        result = _merge_one(root, mapping)
        print(result.message)
        if result.conflicted:
            conflicts += 1
        if result.base_advanced_to is not None:
            mapping["base_revid"] = result.base_advanced_to
            changed = True
    if changed:
        save_workspace_config(root, config)
    return 1 if conflicts else 0


def _merge_one(root, mapping) -> _MergeResult:
    """Three-way merge one mapping's upstream into its working file."""
    remote = mapping.get("remote")
    pageid = mapping.get("pageid")
    fmt = mapping.get("format", "mw")
    local_path = mapping.get("local_path")
    base_revid = mapping.get("base_revid")
    label = f"{remote}:{mapping.get('remote_path')}"

    info = load_page_info(root, remote, pageid)
    if not info or info.get("current_revid") is None:
        return _MergeResult(f"{label}: nothing cached (run fetch)", False, None)
    upstream = info["current_revid"]
    if base_revid is None:
        return _MergeResult(f"{label}: no base revision recorded; cannot merge", False, None)
    if upstream == base_revid:
        return _MergeResult(f"{label}: already up to date (rev {upstream})", False, None)

    base_body = cached_body_path(root, remote, pageid, base_revid)
    upstream_body = cached_body_path(root, remote, pageid, upstream)
    if not base_body.exists():
        return _MergeResult(
            f"{label}: base revision {base_revid} not cached; cannot merge (re-clone)", False, None
        )
    if not upstream_body.exists():
        return _MergeResult(f"{label}: upstream revision {upstream} not cached (run fetch)", False, None)

    base_text = base_body.read_text(encoding="utf-8")
    upstream_text = upstream_body.read_text(encoding="utf-8")
    target = root / local_path

    if not target.exists():
        write_local_body(fmt, target, upstream_text)
        update_cache_base(root, remote, pageid, upstream)
        return _MergeResult(f"{label}: populated {local_path} at rev {upstream}", False, upstream)

    local_text = target.read_text(encoding="utf-8")
    if _has_conflict_markers(local_text):
        return _MergeResult(
            f"{label}: {local_path} has unresolved conflict markers; resolve before merging",
            False,
            None,
        )
    if local_text == base_text:
        write_local_body(fmt, target, upstream_text)
        update_cache_base(root, remote, pageid, upstream)
        return _MergeResult(f"{label}: fast-forwarded {base_revid} -> {upstream}", False, upstream)
    if local_text == upstream_text:
        update_cache_base(root, remote, pageid, upstream)
        return _MergeResult(f"{label}: already matches rev {upstream}; base advanced", False, upstream)

    merged, conflicted = three_way_merge(
        base_text,
        local_text,
        upstream_text,
        mine_label=f"{local_path} (local)",
        other_label=f"{label}@{upstream}",
    )
    write_local_body(fmt, target, merged)
    if conflicted:
        return _MergeResult(
            f"{label}: CONFLICT merging {base_revid}..{upstream} into {local_path}", True, None
        )
    update_cache_base(root, remote, pageid, upstream)
    return _MergeResult(f"{label}: merged {base_revid}..{upstream} into {local_path}", False, upstream)


def _has_conflict_markers(text: str) -> bool:
    """Return whether `text` still contains unresolved merge conflict markers."""
    return any(line.startswith("<<<<<<< ") for line in text.splitlines())
