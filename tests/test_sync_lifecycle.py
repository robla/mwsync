# Offline lifecycle tests for the new fetch/merge/pull commands. A fake remote
# supplies revisions by stable pageid so these exercise workspace config,
# cache/base bookkeeping, and working-tree writes without network access.

from argparse import Namespace

import yaml

from mwmap import sync, workspace
from mwmap.commands.fetch import run_fetch
from mwmap.commands.merge import run_merge
from mwmap.commands.pull import run_pull


class _FakeRemote:
    def __init__(self, name, responses):
        self.name = name
        self._responses = responses

    def fetch_page_by_id(self, pageid):
        body, metadata = self._responses[pageid]
        return body, metadata


def _install_fake_remote(monkeypatch, responses):
    def fake_build_remote(name, _definition):
        return _FakeRemote(name, responses)

    monkeypatch.setattr(sync, "build_remote", fake_build_remote)


def _metadata(revid, *, pageid=1, title="Page"):
    return {
        "pageid": pageid,
        "namespace": 0,
        "namespace_name": "main",
        "title": title,
        "revid": revid,
        "parentid": revid - 1,
        "timestamp": f"2026-01-{revid:02d}T00:00:00Z",
        "contentmodel": "wikitext",
        "redirect": False,
    }


def _write_workspace(root, *, base_text="base\n", local_text="base\n"):
    workspace.init_workspace(root)
    workspace.cache_page_rev(root, "r", base_text, _metadata(10))
    (root / "Page.mw").write_text(local_text, encoding="utf-8")
    config = workspace.initial_config()
    config["remotes"]["r"] = {"type": "fake", "location": "memory://test"}
    workspace.upsert_page_mapping(
        config,
        remote="r",
        pageid=1,
        title="Page",
        local_path="Page.mw",
        fmt="mw",
        base_revid=10,
    )
    workspace.save_workspace_config(root, config)


def _config(root):
    return yaml.safe_load((root / "_mwmap" / "config.yaml").read_text(encoding="utf-8"))


def test_fetch_preserves_working_tree_and_base(tmp_path, monkeypatch):
    # Verifies fetch caches the latest revision but does not touch local/base state.
    _write_workspace(tmp_path, local_text="local edit\n")
    _install_fake_remote(monkeypatch, {1: ("remote\n", _metadata(11))})

    result = run_fetch(Namespace(root=tmp_path, path=None))

    assert result == 0
    assert (tmp_path / "Page.mw").read_text(encoding="utf-8") == "local edit\n"
    assert _config(tmp_path)["mappings"][0]["base_revid"] == 10
    info = workspace.load_page_info(tmp_path, "r", 1)
    assert info["current_revid"] == 11
    assert info["base_revid"] == 10
    assert workspace.cached_body_path(tmp_path, "r", 1, 11).read_text() == "remote\n"


def test_merge_fast_forwards_and_advances_base(tmp_path):
    # Verifies unchanged local content fast-forwards and advances config/cache base.
    _write_workspace(tmp_path)
    workspace.cache_page_rev(tmp_path, "r", "remote\n", _metadata(11))

    result = run_merge(Namespace(root=tmp_path, path=None))

    assert result == 0
    assert (tmp_path / "Page.mw").read_text(encoding="utf-8") == "remote\n"
    assert _config(tmp_path)["mappings"][0]["base_revid"] == 11
    info = workspace.load_page_info(tmp_path, "r", 1)
    assert info["base_revid"] == 11
    assert (workspace.page_cache_dir(tmp_path, "r", 1) / "Page.mw").read_text() == "remote\n"


def test_merge_clean_three_way_advances_base(tmp_path):
    # Verifies disjoint local/upstream edits merge cleanly and advance the base.
    base = "top\nleft\nmiddle\nright\nbottom\n"
    _write_workspace(tmp_path, base_text=base, local_text="top\nLOCAL\nmiddle\nright\nbottom\n")
    workspace.cache_page_rev(
        tmp_path,
        "r",
        "top\nleft\nmiddle\nREMOTE\nbottom\n",
        _metadata(11),
    )

    result = run_merge(Namespace(root=tmp_path, path=None))

    assert result == 0
    assert (tmp_path / "Page.mw").read_text(encoding="utf-8") == (
        "top\nLOCAL\nmiddle\nREMOTE\nbottom\n"
    )
    assert _config(tmp_path)["mappings"][0]["base_revid"] == 11


def test_merge_conflict_leaves_base_unchanged(tmp_path):
    # Verifies conflicting edits write markers and preserve the previous base.
    _write_workspace(tmp_path, base_text="a\nb\nc\n", local_text="a\nLOCAL\nc\n")
    workspace.cache_page_rev(tmp_path, "r", "a\nREMOTE\nc\n", _metadata(11))

    result = run_merge(Namespace(root=tmp_path, path=None))

    assert result == 1
    text = (tmp_path / "Page.mw").read_text(encoding="utf-8")
    assert "<<<<<<< Page.mw (local)" in text
    assert ">>>>>>> r:Page@11" in text
    assert _config(tmp_path)["mappings"][0]["base_revid"] == 10
    assert workspace.load_page_info(tmp_path, "r", 1)["base_revid"] == 10


def test_merge_refuses_unresolved_conflict_markers(tmp_path):
    # Verifies merge does not compound an already-conflicted working file.
    _write_workspace(tmp_path, local_text="<<<<<<< old\nlocal\n=======\nremote\n>>>>>>> old\n")
    workspace.cache_page_rev(tmp_path, "r", "remote\n", _metadata(11))

    result = run_merge(Namespace(root=tmp_path, path=None))

    assert result == 0
    assert _config(tmp_path)["mappings"][0]["base_revid"] == 10
    assert workspace.load_page_info(tmp_path, "r", 1)["base_revid"] == 10


def test_pull_fetches_then_merges(tmp_path, monkeypatch):
    # Verifies pull composes fetch and merge, updating working text and base.
    _write_workspace(tmp_path)
    _install_fake_remote(monkeypatch, {1: ("remote\n", _metadata(11))})

    result = run_pull(Namespace(root=tmp_path, path=None))

    assert result == 0
    assert (tmp_path / "Page.mw").read_text(encoding="utf-8") == "remote\n"
    assert _config(tmp_path)["mappings"][0]["base_revid"] == 11
    info = workspace.load_page_info(tmp_path, "r", 1)
    assert info["current_revid"] == 11
    assert info["base_revid"] == 11
