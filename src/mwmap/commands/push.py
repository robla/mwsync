"""Implementation of `mwmap push`.

Uploads locally edited working files back to their MediaWiki pages (mirrors
`git push`). A direct push for now: the working file is sent as the new page
text, guarded by the mapping's `base_revid` so MediaWiki rejects the edit if
the page changed upstream (surfaced as a "pull then retry" message). On success
the new revision is re-cached and `base_revid` advances.

The login/CSRF/edit primitives are copied from legacy mwsync.py (see
docs/legacy-code-copy.md); credentials come from the environment, never config.

Typical call stack:
  run_push() -> remote.login() once per remote -> sync.push_mapping() per page
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

from mwmap.core.mediawiki import MediaWikiEditConflict, MediaWikiError
from mwmap.core.misc import die
from mwmap.core.textmerge import has_conflict_markers
from mwmap.sync import push_mapping, remote_for_mapping
from mwmap.workspace import (
    cached_body_path,
    iter_page_mappings,
    load_workspace_config,
    save_workspace_config,
)

USER_ENV = "MWMAP_MW_USER"
PASSWORD_ENV = "MWMAP_MW_PASSWORD"


def run_push(args: argparse.Namespace) -> int:
    """Push locally edited working files to their MediaWiki pages."""
    config = load_workspace_config(args.root)
    mappings = iter_page_mappings(config, getattr(args, "path", None))
    if not mappings:
        print("no page mappings to push")
        return 0

    plan, failures = _build_push_plan(args.root, mappings)
    if not plan:
        return 1 if failures else 0

    if getattr(args, "dry_run", False):
        for mapping, text in plan:
            label = f"{mapping.get('remote')}:{mapping.get('remote_path')}"
            print(
                f"would push {label} (page {mapping['pageid']}, "
                f"base {mapping.get('base_revid')}, {len(text)} chars)"
            )
        return 1 if failures else 0

    summary = _resolve_summary(args, plan)
    if not summary:
        die("empty edit summary; aborting push")

    username = os.environ.get(USER_ENV, "")
    password = os.environ.get(PASSWORD_ENV, "")
    if not username or not password:
        die(f"push requires credentials; set {USER_ENV} and {PASSWORD_ENV}")

    remotes: dict = {}
    logged_in: set[str] = set()
    changed = False
    for mapping, text in plan:
        label = f"{mapping.get('remote')}:{mapping.get('remote_path')}"
        remote = remote_for_mapping(config, mapping, remotes)
        if remote.name not in logged_in:
            try:
                remote.login(username, password)
            except MediaWikiError as exc:
                die(f"login to remote {remote.name!r} failed: {exc}")
            logged_in.add(remote.name)
        try:
            new_revid = push_mapping(args.root, remote, mapping, text, summary)
        except MediaWikiEditConflict as exc:
            print(
                f"{label}: {exc}; run 'mwmap pull {mapping['local_path']}' then retry",
                file=sys.stderr,
            )
            failures += 1
            continue
        except MediaWikiError as exc:
            print(f"{label}: push failed: {exc}", file=sys.stderr)
            failures += 1
            continue
        mapping["base_revid"] = new_revid
        changed = True
        print(f"{label}: pushed rev {new_revid}")

    if changed:
        save_workspace_config(args.root, config)
    return 1 if failures else 0


def _build_push_plan(root, mappings):
    """Return (pushable [(mapping, local_text)], failure_count).

    Skips pages with no working file or no local changes; counts a working file
    with unresolved conflict markers as a failure (refusing to upload markers).
    """
    plan = []
    failures = 0
    for mapping in mappings:
        label = f"{mapping.get('remote')}:{mapping.get('remote_path')}"
        target = root / mapping["local_path"]
        if not target.exists():
            print(f"{label}: no working file at {mapping['local_path']}; skipping")
            continue
        local_text = target.read_text(encoding="utf-8")
        if has_conflict_markers(local_text):
            print(
                f"{label}: unresolved conflict markers in {mapping['local_path']}; "
                "resolve before pushing",
                file=sys.stderr,
            )
            failures += 1
            continue
        base_body = cached_body_path(root, mapping["remote"], mapping["pageid"], mapping.get("base_revid"))
        if base_body.exists() and base_body.read_text(encoding="utf-8") == local_text:
            print(f"{label}: up to date (rev {mapping.get('base_revid')}); nothing to push")
            continue
        plan.append((mapping, local_text))
    return plan, failures


def _resolve_summary(args, plan):
    """Return the edit summary from -m, or prompt an editor for a single page."""
    summary = getattr(args, "summary", None)
    if summary:
        return summary.strip()
    if len(plan) > 1:
        die("pushing multiple pages requires -m/--summary")
    return _prompt_summary(plan[0][0])


def _prompt_summary(mapping):
    """Open $VISUAL/$EDITOR for an edit summary (copied from mwsync _edit_summary)."""
    comment = (
        f"\n# Edit summary for pushing {mapping.get('remote')}:{mapping.get('remote_path')}.\n"
        "# Lines starting with '#' are stripped; an empty summary aborts the push.\n"
        f"# Base revid: {mapping.get('base_revid')}\n"
    )
    fd, tmp_path = tempfile.mkstemp(suffix=".mwmap-summary.txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(comment)
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
