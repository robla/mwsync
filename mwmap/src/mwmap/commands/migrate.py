"""Implementation of `mwmap migrate` for legacy page mappings.

Typical call stack:
  run_migrate() -> _migrate_page_mapping() -> save_workspace_config()
"""

from __future__ import annotations

import argparse
from typing import Any

from mwmap.core.misc import die
from mwmap.workspace import load_workspace_config, save_workspace_config


_LEGACY_UPSTREAM_FIELDS = {"remote", "pageid", "remote_path", "base_revid"}
_REQUIRED_LEGACY_FIELDS = _LEGACY_UPSTREAM_FIELDS | {"local_path", "format"}


def run_migrate(args: argparse.Namespace) -> int:
    """Upgrade one legacy page mapping, or every mapping with `--all`."""
    config = load_workspace_config(args.root)
    mappings = config.get("mappings") or []
    legacy = [
        (index, mapping)
        for index, mapping in enumerate(mappings)
        if _is_legacy_page_mapping(mapping)
    ]

    path = getattr(args, "path", None)
    migrate_all = getattr(args, "all", False)
    if path and migrate_all:
        die("migrate accepts either PATH or --all, not both")

    if migrate_all:
        selected = legacy
    elif path:
        matches = [item for item in legacy if item[1].get("local_path") == path]
        if not matches:
            current = [
                mapping
                for mapping in mappings
                if mapping.get("type") == "page" and mapping.get("local_path") == path
            ]
            if current and "upstreams" in current[0]:
                print(f"{path}: already migrated")
                return 0
            die(f"no legacy page mapping for path: {path}")
        if len(matches) > 1:
            die(f"multiple legacy page mappings use local path: {path}")
        selected = matches
    else:
        if len(legacy) > 1:
            choices = "\n".join(f"  {mapping.get('local_path', '?')}" for _, mapping in legacy)
            die(
                "migrate needs a local path when multiple legacy mappings remain:\n"
                f"{choices}\nUse 'mwmap migrate PATH' or 'mwmap migrate --all'."
            )
        selected = legacy

    if not selected:
        print("no legacy page mappings to migrate")
        return 0

    replacements = [
        (index, _migrate_page_mapping(mapping)) for index, mapping in selected
    ]
    for index, mapping in replacements:
        mappings[index] = mapping

    # The current schema is self-describing; version numbers are not load-bearing.
    config.pop("version", None)
    config["mappings"] = mappings
    save_workspace_config(args.root, config)

    for _, mapping in replacements:
        print(f"{mapping['local_path']}: migrated")
    print(f"migrated {len(replacements)} page mapping(s)")
    return 0


def _is_legacy_page_mapping(mapping: dict[str, Any]) -> bool:
    """Return whether a page mapping still uses top-level upstream fields."""
    return mapping.get("type") == "page" and "upstreams" not in mapping


def _migrate_page_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return one legacy page mapping in the multi-upstream schema."""
    missing = sorted(field for field in _REQUIRED_LEGACY_FIELDS if field not in mapping)
    if missing:
        path = mapping.get("local_path", "?")
        die(f"cannot migrate {path}: missing fields: {', '.join(missing)}")

    remote = mapping["remote"]
    if not remote:
        die(f"cannot migrate {mapping['local_path']}: remote is empty")

    migrated = {
        "type": "page",
        "local_path": mapping["local_path"],
        "format": mapping["format"],
    }
    known_fields = _REQUIRED_LEGACY_FIELDS | {"type"}
    for key, value in mapping.items():
        if key not in known_fields:
            migrated[key] = value
    migrated["primary_upstream"] = remote
    migrated["upstreams"] = {
        remote: {
            "remote": remote,
            "pageid": mapping["pageid"],
            "remote_path": mapping["remote_path"],
            "base_revid": mapping["base_revid"],
            "state": "tracked",
        }
    }
    return migrated
