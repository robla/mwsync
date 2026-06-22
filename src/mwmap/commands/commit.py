"""Implementation of `mwmap commit`.

Stages a pending edit for each changed working file (mirrors the legacy
`mwsync.py commit`, adapted to pageid-keyed cache dirs; see
docs/legacy-code-copy.md). `commit` records *what* to upload and the edit
summary; `push` later submits it. This is the Git-like split the user wanted:
edit -> commit -> push, rather than pushing the working tree directly.

A pending commit is stored as `commit.mw` (body) + `commit.json` (summary,
base_revid, title) under the page's cache dir. Re-running merge/pull does not
auto-update a pending commit; re-`commit` after resolving.

Typical call stack:
  run_commit() -> _build_candidates() -> workspace.write_pending_commit() per page
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import tempfile

from mwmap.core.misc import die
from mwmap.core.textmerge import has_conflict_markers
from mwmap.workspace import (
    cached_body_path,
    iter_page_mappings,
    load_workspace_config,
    read_pending_commit,
    write_pending_commit,
)


def run_commit(args: argparse.Namespace) -> int:
    """Stage pending edits for changed working files (run `push` to publish)."""
    config = load_workspace_config(args.root)
    mappings = iter_page_mappings(config, getattr(args, "path", None))
    if not mappings:
        print("no page mappings to commit")
        return 0

    candidates, failures = _build_candidates(args, mappings)
    if not candidates:
        return 1 if failures else 0

    summary = _resolve_summary(args, candidates)
    if not summary:
        die("empty edit summary; aborting commit")

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for mapping, text, existing in candidates:
        meta = {
            "remote": mapping["remote"],
            "pageid": mapping["pageid"],
            "title": mapping.get("remote_path"),
            "local_path": mapping["local_path"],
            "base_revid": mapping.get("base_revid"),
            "summary": summary,
            "created_at": now,
        }
        write_pending_commit(args.root, mapping["remote"], mapping["pageid"], meta, text)
        verb = "amended" if existing else "committed"
        label = f"{mapping['remote']}:{mapping.get('remote_path')}"
        print(f"{label}: {verb} ({len(text)} chars, base {mapping.get('base_revid')})")
    print("Run 'mwmap push' to publish.")
    return 1 if failures else 0


def _build_candidates(args, mappings):
    """Return (committable [(mapping, text, existing_pending)], failure_count)."""
    candidates = []
    failures = 0
    for mapping in mappings:
        label = f"{mapping.get('remote')}:{mapping.get('remote_path')}"
        target = args.root / mapping["local_path"]
        if not target.exists():
            print(f"{label}: no working file at {mapping['local_path']}; skipping")
            continue
        text = target.read_text(encoding="utf-8")
        if has_conflict_markers(text):
            print(
                f"{label}: unresolved conflict markers in {mapping['local_path']}; "
                "resolve before committing",
                file=sys.stderr,
            )
            failures += 1
            continue
        existing = read_pending_commit(args.root, mapping["remote"], mapping["pageid"])
        if existing and not getattr(args, "amend", False):
            print(
                f"{label}: a pending commit already exists; run 'mwmap push' "
                "or 'mwmap commit --amend' to replace it",
                file=sys.stderr,
            )
            failures += 1
            continue
        base_body = cached_body_path(
            args.root, mapping["remote"], mapping["pageid"], mapping.get("base_revid")
        )
        if (
            not getattr(args, "allow_empty", False)
            and base_body.exists()
            and base_body.read_text(encoding="utf-8") == text
        ):
            print(f"{label}: nothing to commit (matches rev {mapping.get('base_revid')})")
            continue
        candidates.append((mapping, text, existing))
    return candidates, failures


def _resolve_summary(args, candidates):
    """Return the edit summary from -m, or prompt an editor for a single page."""
    summary = getattr(args, "message", None)
    if summary:
        return summary.strip()
    if len(candidates) > 1:
        die("committing multiple pages requires -m/--message")
    mapping, _text, existing = candidates[0]
    default = str(existing.get("summary", "")) if existing else ""
    return _prompt_summary(mapping, default)


def _prompt_summary(mapping, default=""):
    """Open $VISUAL/$EDITOR for an edit summary (copied from mwsync _edit_summary)."""
    comment = (
        f"\n# Edit summary for committing {mapping.get('remote')}:{mapping.get('remote_path')}.\n"
        "# Lines starting with '#' are stripped; an empty summary aborts the commit.\n"
        f"# Base revid: {mapping.get('base_revid')}\n"
    )
    fd, tmp_path = tempfile.mkstemp(suffix=".mwmap-summary.txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(default + comment)
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        subprocess.run([editor, tmp_path])
        with open(tmp_path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    lines = [line for line in raw.splitlines() if not line.startswith("#")]
    return "\n".join(lines).strip()
