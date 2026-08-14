"""Implementation of `mwmap push`.

Submits the pending commit staged by `mwmap commit` for each paired page to its
MediaWiki page (mirrors `git push`: you push what you committed, not the working
tree). The edit is guarded by the staged `base_revid`, so an upstream change
since that base is rejected as an edit conflict (resolve with `pull`, re-`commit`,
then retry). On success the new revision is re-cached, `base_revid` advances, and
the pending commit is cleared.

The login/CSRF/edit primitives are copied from legacy mwsync.py (see
docs/legacy-code-copy.md); credentials come from the environment, never config.

Typical call stack:
  run_push() -> remote.login() once per remote -> sync.push_mapping() per page
"""

from __future__ import annotations

import argparse
import os
import sys

from mwmap.core.mediawiki import MediaWikiEditConflict, MediaWikiError
from mwmap.core.misc import die
from mwmap.sync import push_mapping, remote_for_mapping
from mwmap.workspace import (
    clear_pending_commit,
    iter_page_mappings,
    load_workspace_config,
    pending_commit_body_path,
    read_pending_commit,
    save_workspace_config,
)

USER_ENV = "MWMAP_MW_USER"
PASSWORD_ENV = "MWMAP_MW_PASSWORD"


def run_push(args: argparse.Namespace) -> int:
    """Publish staged commits to MediaWiki (run `commit` first)."""
    config = load_workspace_config(args.root)
    mappings = iter_page_mappings(config, getattr(args, "path", None))
    if not mappings:
        print("no page mappings to push")
        return 0

    plan = []  # (mapping, meta, body)
    for mapping in mappings:
        label = f"{mapping.get('remote')}:{mapping.get('remote_path')}"
        meta = read_pending_commit(args.root, mapping["remote"], mapping["pageid"])
        if meta is None:
            print(f"{label}: nothing to push (no pending commit; run 'mwmap commit' first)")
            continue
        body = pending_commit_body_path(
            args.root, mapping["remote"], mapping["pageid"]
        ).read_text(encoding="utf-8")
        plan.append((mapping, meta, body))

    if not plan:
        return 0

    if getattr(args, "dry_run", False):
        for mapping, meta, body in plan:
            label = f"{mapping.get('remote')}:{mapping.get('remote_path')}"
            print(
                f"would push {label} (page {mapping['pageid']}, base "
                f"{meta.get('base_revid')}, {len(body)} chars): {meta.get('summary')}"
            )
        return 0

    username = os.environ.get(USER_ENV, "")
    password = os.environ.get(PASSWORD_ENV, "")
    if not username or not password:
        die(f"push requires credentials; set {USER_ENV} and {PASSWORD_ENV}")

    remotes: dict = {}
    logged_in: set[str] = set()
    changed = False
    failures = 0
    for mapping, meta, body in plan:
        label = f"{mapping.get('remote')}:{mapping.get('remote_path')}"
        remote = remote_for_mapping(config, mapping, remotes)
        if remote.name not in logged_in:
            try:
                remote.login(username, password)
            except MediaWikiError as exc:
                die(f"login to remote {remote.name!r} failed: {exc}")
            logged_in.add(remote.name)
        try:
            new_revid = push_mapping(
                args.root, remote, mapping, body, meta.get("summary", ""), meta.get("base_revid")
            )
        except MediaWikiEditConflict as exc:
            print(
                f"{label}: {exc}; run 'mwmap pull {mapping['local_path']}', "
                "re-commit, then retry (pending commit kept)",
                file=sys.stderr,
            )
            failures += 1
            continue
        except MediaWikiError as exc:
            print(f"{label}: push failed: {exc}", file=sys.stderr)
            failures += 1
            continue
        mapping["base_revid"] = new_revid
        clear_pending_commit(args.root, mapping["remote"], mapping["pageid"])
        changed = True
        print(f"{label}: pushed rev {new_revid}")

    if changed:
        save_workspace_config(args.root, config)
    return 1 if failures else 0
