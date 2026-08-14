"""Implementation of `mwmap fsck` — check cache and mapping integrity.

Cache writes are atomic per file but not as a set, so a crash mid-fetch can
leave a revision with a body but no sidecar/history (or vice versa). `fsck`
surfaces those inconsistencies rather than relying on locking. Errors make
fsck exit nonzero (like `git fsck`); warnings are advisory.

Typical call stack:
  run_fsck() -> walk cache/<remote>/pages + config mappings -> report
"""

from __future__ import annotations

import argparse
import json
import sys

import yaml

from mwmap.workspace import cache_dir, load_workspace_config, page_cache_dir


def run_fsck(args: argparse.Namespace) -> int:
    """Report cache/mapping inconsistencies; exit nonzero if any errors found."""
    config = load_workspace_config(args.root)
    errors: list[str] = []
    warnings: list[str] = []

    cache = cache_dir(args.root)
    if cache.exists():
        for remote_dir in sorted(p for p in cache.iterdir() if p.is_dir()):
            pages_dir = remote_dir / "pages"
            if not pages_dir.exists():
                continue
            for page_dir in sorted(p for p in pages_dir.iterdir() if p.is_dir()):
                _check_page_dir(page_dir, errors, warnings)
            _check_title_aliases(remote_dir, errors, warnings)

    _check_mappings(args, config, errors, warnings)

    for warning in warnings:
        print(f"warning: {warning}")
    for problem in errors:
        print(f"error: {problem}", file=sys.stderr)

    if errors:
        print(f"fsck: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"fsck: ok ({len(warnings)} warning(s))" if warnings else "fsck: ok")
    return 0


def _check_page_dir(page_dir, errors: list[str], warnings: list[str]) -> None:
    """Validate one cache/<remote>/pages/<pageid>/ directory's revision files."""
    page_yaml = page_dir / "page.yaml"
    if not page_yaml.exists():
        errors.append(f"{page_dir}: missing page.yaml")
    else:
        try:
            info = yaml.safe_load(page_yaml.read_text(encoding="utf-8")) or {}
            if str(info.get("pageid")) != page_dir.name:
                warnings.append(
                    f"{page_dir}: page.yaml pageid {info.get('pageid')} "
                    f"!= directory name {page_dir.name}"
                )
        except yaml.YAMLError as exc:
            errors.append(f"{page_dir}: unparseable page.yaml: {exc}")

    history = page_dir / "history.jsonl"
    if not history.exists():
        errors.append(f"{page_dir}: missing history.jsonl")
        return

    revisions = 0
    for line_number, line in enumerate(
        history.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{history}:{line_number}: not valid JSON: {exc}")
            continue
        revid = record.get("revid")
        if revid is None:
            errors.append(f"{history}:{line_number}: record has no revid")
            continue
        revisions += 1
        body = record.get("body") or f"{revid}.mw"
        if not (page_dir / body).exists():
            errors.append(f"{page_dir}: history references missing body {body} (rev {revid})")
        if not (page_dir / f"{revid}.yaml").exists():
            errors.append(f"{page_dir}: missing metadata sidecar {revid}.yaml (rev {revid})")
    if revisions == 0:
        warnings.append(f"{page_dir}: history.jsonl has no revisions")


def _check_title_aliases(remote_dir, errors: list[str], warnings: list[str]) -> None:
    """Validate symlink aliases in cache/<remote>/by-title/ when present."""
    aliases = remote_dir / "by-title"
    if not aliases.exists():
        return
    for alias in sorted(p for p in aliases.rglob("*") if p.is_symlink()):
        if not alias.exists():
            errors.append(f"{alias}: broken title alias")


def _check_mappings(args, config, errors: list[str], warnings: list[str]) -> None:
    """Cross-check config mappings against remotes and the cache."""
    remotes = config.get("remotes") or {}
    for mapping in config.get("mappings") or []:
        local_path = mapping.get("local_path")
        remote = mapping.get("remote")
        if remote not in remotes:
            errors.append(f"mapping {local_path!r} references unknown remote {remote!r}")
        if local_path and not (args.root / local_path).exists():
            warnings.append(f"mapping local file missing: {local_path}")

        pageid = mapping.get("pageid")
        base_revid = mapping.get("base_revid")
        if remote in remotes and pageid is not None and base_revid is not None:
            history = page_cache_dir(args.root, str(remote), pageid) / "history.jsonl"
            if history.exists() and str(base_revid) not in _history_revids(history):
                warnings.append(
                    f"base_revid {base_revid} not found in cache for {remote}:{pageid}"
                )


def _history_revids(history) -> set[str]:
    """Return the set of revid strings recorded in a history.jsonl file."""
    revids: set[str] = set()
    for line in history.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "revid" in record:
            revids.add(str(record["revid"]))
    return revids
