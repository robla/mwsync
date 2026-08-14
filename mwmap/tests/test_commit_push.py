# Offline lifecycle tests for the commit/push two-step. `commit` is purely local
# (stages commit.mw + commit.json in the page cache dir); `push` uses a fake
# writable remote (login/push_page/fetch_page_by_id over an in-memory server) so
# the edit-conflict guard, base advance, and pending-commit clearing are exercised
# without network access. Credentials come from monkeypatched env vars.

from argparse import Namespace

import pytest
import yaml

from mwmap import sync, workspace
from mwmap.commands.commit import run_commit
from mwmap.commands.push import run_push
from mwmap.core.mediawiki import MediaWikiEditConflict


class _FakeWritableRemote:
    def __init__(self, name, server):
        self.name = name
        self.server = server  # {"revid": int, "content": str, "summary": str|None}
        self.logged_in = False

    def login(self, username, password):
        self.logged_in = True

    def push_page(self, pageid, text, baserevid, summary):
        if int(baserevid) != int(self.server["revid"]):
            raise MediaWikiEditConflict(
                f"page changed upstream since revid {baserevid}"
            )
        self.server["revid"] += 1
        self.server["content"] = text
        self.server["summary"] = summary
        return self.server["revid"]

    def fetch_page_by_id(self, pageid):
        return self.server["content"], _metadata(self.server["revid"])


def _install_writable_remote(monkeypatch, server):
    remote = _FakeWritableRemote("r", server)
    monkeypatch.setattr(sync, "build_remote", lambda name, _definition: remote)
    return remote


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
    return yaml.safe_load((root / "_mwmap" / "mwmap.yaml").read_text(encoding="utf-8"))


def _commit_args(root, **kwargs):
    return Namespace(
        root=root,
        path=kwargs.get("path"),
        message=kwargs.get("message"),
        amend=kwargs.get("amend", False),
        allow_empty=kwargs.get("allow_empty", False),
    )


def _push_args(root, **kwargs):
    return Namespace(root=root, path=kwargs.get("path"), dry_run=kwargs.get("dry_run", False))


def _set_credentials(monkeypatch):
    monkeypatch.setenv("MWMAP_MW_USER", "bot")
    monkeypatch.setenv("MWMAP_MW_PASSWORD", "pw")


# --- commit (local-only) ---------------------------------------------------

def test_commit_stages_pending_edit(tmp_path):
    # Verifies commit records body + summary + base, without touching base or the working file.
    _write_workspace(tmp_path, local_text="changed\n")

    assert run_commit(_commit_args(tmp_path, message="tweak")) == 0

    meta = workspace.read_pending_commit(tmp_path, "r", 1)
    assert meta is not None
    assert meta["summary"] == "tweak"
    assert meta["base_revid"] == 10
    body = workspace.pending_commit_body_path(tmp_path, "r", 1).read_text(encoding="utf-8")
    assert body == "changed\n"
    # commit is local: working file and config base are untouched.
    assert (tmp_path / "Page.mw").read_text(encoding="utf-8") == "changed\n"
    assert _config(tmp_path)["mappings"][0]["base_revid"] == 10


def test_commit_skips_unchanged_working_file(tmp_path):
    # Verifies an unchanged file (matching base) stages nothing.
    _write_workspace(tmp_path, local_text="base\n")

    assert run_commit(_commit_args(tmp_path, message="noop")) == 0
    assert workspace.read_pending_commit(tmp_path, "r", 1) is None


def test_commit_refuses_conflict_markers(tmp_path):
    # Verifies a working file with conflict markers is not committable.
    _write_workspace(tmp_path, local_text="a\n<<<<<<< x\nb\n=======\nc\n>>>>>>> y\n")

    assert run_commit(_commit_args(tmp_path, message="bad")) == 1
    assert workspace.read_pending_commit(tmp_path, "r", 1) is None


def test_commit_amend_replaces_existing_pending(tmp_path):
    # Verifies a second commit needs --amend, which then replaces the staged edit.
    _write_workspace(tmp_path, local_text="first\n")
    assert run_commit(_commit_args(tmp_path, message="first")) == 0

    (tmp_path / "Page.mw").write_text("second\n", encoding="utf-8")
    # Without --amend the existing pending commit blocks a re-commit.
    assert run_commit(_commit_args(tmp_path, message="second")) == 1
    assert workspace.read_pending_commit(tmp_path, "r", 1)["summary"] == "first"

    assert run_commit(_commit_args(tmp_path, message="second", amend=True)) == 0
    meta = workspace.read_pending_commit(tmp_path, "r", 1)
    assert meta["summary"] == "second"
    assert workspace.pending_commit_body_path(tmp_path, "r", 1).read_text() == "second\n"


# --- push (consumes the pending commit) ------------------------------------

def test_push_without_pending_commit_is_noop(tmp_path, monkeypatch):
    # Verifies push does nothing (rc 0) when nothing has been committed.
    _write_workspace(tmp_path, local_text="changed\n")
    _install_writable_remote(monkeypatch, {"revid": 10, "content": "base\n"})
    _set_credentials(monkeypatch)

    assert run_push(_push_args(tmp_path)) == 0
    assert _config(tmp_path)["mappings"][0]["base_revid"] == 10


def test_push_publishes_pending_and_advances_base(tmp_path, monkeypatch):
    # Verifies push submits the staged edit, advances base, and clears the pending commit.
    _write_workspace(tmp_path, local_text="changed\n")
    server = {"revid": 10, "content": "base\n"}
    remote = _install_writable_remote(monkeypatch, server)
    _set_credentials(monkeypatch)
    assert run_commit(_commit_args(tmp_path, message="tweak")) == 0

    assert run_push(_push_args(tmp_path)) == 0
    assert remote.logged_in is True
    assert server["content"] == "changed\n"
    assert server["summary"] == "tweak"
    assert _config(tmp_path)["mappings"][0]["base_revid"] == 11
    assert workspace.read_pending_commit(tmp_path, "r", 1) is None
    assert workspace.cached_body_path(tmp_path, "r", 1, 11).read_text() == "changed\n"
    # push leaves the working file alone.
    assert (tmp_path / "Page.mw").read_text(encoding="utf-8") == "changed\n"


def test_push_edit_conflict_keeps_pending_and_base(tmp_path, monkeypatch):
    # Verifies an upstream change since base aborts the push, preserving staged state.
    _write_workspace(tmp_path, local_text="changed\n")
    # Server is already ahead of the local base (revid 11 vs base 10).
    _install_writable_remote(monkeypatch, {"revid": 11, "content": "upstream\n"})
    _set_credentials(monkeypatch)
    assert run_commit(_commit_args(tmp_path, message="tweak")) == 0

    assert run_push(_push_args(tmp_path)) == 1
    assert workspace.read_pending_commit(tmp_path, "r", 1) is not None
    assert _config(tmp_path)["mappings"][0]["base_revid"] == 10


def test_push_dry_run_contacts_nothing(tmp_path, monkeypatch):
    # Verifies --dry-run reports the plan without editing the remote or clearing state.
    _write_workspace(tmp_path, local_text="changed\n")
    server = {"revid": 10, "content": "base\n"}
    _install_writable_remote(monkeypatch, server)
    _set_credentials(monkeypatch)
    assert run_commit(_commit_args(tmp_path, message="tweak")) == 0

    assert run_push(_push_args(tmp_path, dry_run=True)) == 0
    assert server["revid"] == 10
    assert workspace.read_pending_commit(tmp_path, "r", 1) is not None


def test_push_requires_credentials(tmp_path, monkeypatch):
    # Verifies push refuses to run without MWMAP_MW_USER / MWMAP_MW_PASSWORD.
    _write_workspace(tmp_path, local_text="changed\n")
    _install_writable_remote(monkeypatch, {"revid": 10, "content": "base\n"})
    monkeypatch.delenv("MWMAP_MW_USER", raising=False)
    monkeypatch.delenv("MWMAP_MW_PASSWORD", raising=False)
    assert run_commit(_commit_args(tmp_path, message="tweak")) == 0

    with pytest.raises(SystemExit):
        run_push(_push_args(tmp_path))
