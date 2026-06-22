# Verifies fsck catches the partial-revision case its existence is justified by:
# a history record whose body file is missing (a crash between the body write and
# history write) is reported as an error and makes fsck exit nonzero, while a
# clean cache passes. Runs in-process against a tmp workspace; no network.

import argparse

from mwmap import workspace
from mwmap.commands.fsck import run_fsck


def _args(root):
    return argparse.Namespace(root=root)


def _cache_one_page(root):
    metadata = {
        "pageid": 1,
        "title": "Alpha",
        "revid": 5,
        "parentid": 0,
        "timestamp": "2026-01-01T00:00:00Z",
        "contentmodel": "wikitext",
        "redirect": False,
    }
    workspace.cache_page_rev(root, "r", "body", metadata)


def test_fsck_passes_clean_cache(tmp_path, capsys):
    workspace.init_workspace(tmp_path)
    _cache_one_page(tmp_path)
    assert run_fsck(_args(tmp_path)) == 0
    assert "ok" in capsys.readouterr().out


def test_fsck_flags_missing_body(tmp_path, capsys):
    workspace.init_workspace(tmp_path)
    _cache_one_page(tmp_path)
    (tmp_path / "_mwmap" / "cache" / "r" / "1" / "5.mw").unlink()

    assert run_fsck(_args(tmp_path)) == 1
    assert "missing body" in capsys.readouterr().err
