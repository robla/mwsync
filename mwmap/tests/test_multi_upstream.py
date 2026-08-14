# Offline compatibility tests for legacy and multi-upstream mappings. Fake
# remotes exercise per-upstream fetch/merge/pull/commit/push/preview state
# without contacting MediaWiki; fsck and status use the same mixed schema.

from argparse import Namespace

import yaml

from mwmap import sync, workspace
from mwmap.cli import build_cli_parser
from mwmap.commands.commit import run_commit
from mwmap.commands.fetch import run_fetch
from mwmap.commands.fsck import run_fsck
from mwmap.commands.merge import run_merge
from mwmap.commands.preview import run_preview
from mwmap.commands.pull import run_pull
from mwmap.commands.push import run_push
from mwmap.commands.status import run_status


def _metadata(name, revid, *, pageid, title):
    return {
        "pageid": pageid,
        "namespace": 0,
        "namespace_name": "main",
        "title": title,
        "revid": revid,
        "parentid": max(0, revid - 1),
        "timestamp": f"2026-08-13T00:{revid % 60:02d}:00Z",
        "contentmodel": "wikitext",
        "redirect": False,
        "remote": name,
    }


def _tracked_upstream(remote, pageid, title, base_revid):
    return {
        "remote": remote,
        "pageid": pageid,
        "remote_path": title,
        "base_revid": base_revid,
        "state": "tracked",
    }


def _multi_mapping(*, mirror_created=True):
    mirror = (
        _tracked_upstream("mirror", 2, "Page", 20)
        if mirror_created
        else {
            "remote": "mirror",
            "pageid": None,
            "remote_path": "Page",
            "base_revid": None,
            "state": "not_created",
        }
    )
    return {
        "type": "page",
        "local_path": "Page.mw",
        "format": "mw",
        "primary_upstream": "primary",
        "upstreams": {
            "primary": _tracked_upstream("primary", 1, "Page", 10),
            "mirror": mirror,
        },
    }


def _legacy_mapping():
    return {
        "type": "page",
        "remote": "legacy",
        "pageid": 3,
        "format": "mw",
        "remote_path": "Legacy",
        "local_path": "Legacy.mw",
        "base_revid": 30,
    }


def _write_workspace(root, *, mirror_created=True, local_text="primary base\n", mixed=False):
    workspace.init_workspace(root)
    workspace.cache_page_rev(
        root,
        "primary",
        "primary base\n",
        _metadata("primary", 10, pageid=1, title="Page"),
    )
    if mirror_created:
        workspace.cache_page_rev(
            root,
            "mirror",
            "mirror base\n",
            _metadata("mirror", 20, pageid=2, title="Page"),
        )
    (root / "Page.mw").write_text(local_text, encoding="utf-8")

    config = {
        "remotes": {
            "primary": {"type": "fake", "location": "memory://primary"},
            "mirror": {"type": "fake", "location": "memory://mirror"},
            "legacy": {"type": "fake", "location": "memory://legacy"},
        },
        "mappings": [_multi_mapping(mirror_created=mirror_created)],
    }
    if mixed:
        (root / "Legacy.mw").write_text("legacy base\n", encoding="utf-8")
        config["mappings"].append(_legacy_mapping())
    workspace.save_workspace_config(root, config)
    return config


def _config(root):
    return yaml.safe_load((root / "_mwmap" / "mwmap.yaml").read_text(encoding="utf-8"))


class _FakeRemote:
    def __init__(self, name, server, calls):
        self.name = name
        self.server = server
        self.calls = calls

    def fetch_page_by_id(self, pageid):
        self.calls.append((self.name, "fetch", pageid))
        return self.server["content"], _metadata(
            self.name,
            self.server["revid"],
            pageid=self.server["pageid"],
            title=self.server["title"],
        )

    def preview_wikitext(self, title, wikitext):
        self.calls.append((self.name, "preview", title, wikitext))
        return {"text": f"<p>{self.name} preview</p>", "displaytitle": title}

    def login(self, username, password):
        self.calls.append((self.name, "login", username))

    def push_page(self, pageid, text, baserevid, summary):
        self.calls.append((self.name, "push", pageid, baserevid, text, summary))
        assert int(baserevid) == int(self.server["revid"])
        self.server["revid"] += 1
        self.server["content"] = text
        return self.server["revid"]

    def create_page(self, title, text, summary):
        self.calls.append((self.name, "create", title, text, summary))
        self.server.update({"pageid": 22, "revid": 1, "title": title, "content": text})
        return _metadata(self.name, 1, pageid=22, title=title)


def _install_remotes(monkeypatch, servers):
    calls = []

    def build(name, _definition):
        return _FakeRemote(name, servers[name], calls)

    monkeypatch.setattr(sync, "build_remote", build)
    return calls


def _servers():
    return {
        "primary": {"pageid": 1, "revid": 11, "title": "Page", "content": "primary new\n"},
        "mirror": {"pageid": 2, "revid": 21, "title": "Page", "content": "mirror new\n"},
        "legacy": {"pageid": 3, "revid": 31, "title": "Legacy", "content": "legacy new\n"},
    }


def _commit_args(root, **kwargs):
    return Namespace(
        root=root,
        path="Page.mw",
        message=kwargs.get("message", "mirror edit"),
        amend=False,
        allow_empty=False,
    )


def _push_args(root, upstream):
    return Namespace(root=root, path="Page.mw", upstream=upstream, dry_run=False)


def _credentials(monkeypatch):
    monkeypatch.setenv("MWMAP_MW_USER", "bot")
    monkeypatch.setenv("MWMAP_MW_PASSWORD", "pw")


def test_cli_accepts_multi_upstream_selectors_and_status_path():
    # Ensures public CLI syntax exposes targeted fetch/push and per-article status.
    parser = build_cli_parser()

    fetch = parser.parse_args(["fetch", "Page.mw", "--upstream", "mirror"])
    push = parser.parse_args(["push", "Page.mw", "--upstream", "mirror"])
    status = parser.parse_args(["status", "Page.mw"])

    assert fetch.upstream == "mirror"
    assert push.upstream == "mirror"
    assert status.path == "Page.mw"


def test_status_reports_migrated_upstreams_and_legacy_mapping(tmp_path, capsys):
    # Verifies migrated and legacy mappings remain readable side-by-side during rollout.
    _write_workspace(tmp_path, mixed=True)

    assert run_status(Namespace(root=tmp_path, path=None)) == 0

    output = capsys.readouterr().out
    assert "primary:Page" in output
    assert "mirror:Page" in output
    assert "tracked" in output
    assert "legacy:Legacy" in output


def test_fetch_defaults_to_all_tracked_upstreams(tmp_path, monkeypatch):
    # Verifies fetch updates every tracked upstream cache without advancing either base.
    _write_workspace(tmp_path)
    calls = _install_remotes(monkeypatch, _servers())

    assert run_fetch(Namespace(root=tmp_path, path="Page.mw", upstream=None)) == 0

    assert ("primary", "fetch", 1) in calls
    assert ("mirror", "fetch", 2) in calls
    mapping = _config(tmp_path)["mappings"][0]
    assert mapping["upstreams"]["primary"]["base_revid"] == 10
    assert mapping["upstreams"]["mirror"]["base_revid"] == 20


def test_fetch_upstream_limits_network_and_cache_changes(tmp_path, monkeypatch):
    # Verifies --upstream fetch contacts only the selected upstream.
    _write_workspace(tmp_path)
    calls = _install_remotes(monkeypatch, _servers())

    assert run_fetch(Namespace(root=tmp_path, path="Page.mw", upstream="mirror")) == 0

    assert ("mirror", "fetch", 2) in calls
    assert not any(call[0] == "primary" for call in calls)
    assert workspace.load_page_info(tmp_path, "mirror", 2)["current_revid"] == 21
    assert workspace.load_page_info(tmp_path, "primary", 1)["current_revid"] == 10


def test_merge_uses_primary_and_advances_only_its_base(tmp_path):
    # Verifies default merge integrates the primary upstream and leaves mirror state alone.
    _write_workspace(tmp_path)
    workspace.cache_page_rev(
        tmp_path,
        "primary",
        "primary new\n",
        _metadata("primary", 11, pageid=1, title="Page"),
    )
    workspace.cache_page_rev(
        tmp_path,
        "mirror",
        "mirror new\n",
        _metadata("mirror", 21, pageid=2, title="Page"),
    )

    assert run_merge(Namespace(root=tmp_path, path="Page.mw")) == 0

    mapping = _config(tmp_path)["mappings"][0]
    assert (tmp_path / "Page.mw").read_text(encoding="utf-8") == "primary new\n"
    assert mapping["upstreams"]["primary"]["base_revid"] == 11
    assert mapping["upstreams"]["mirror"]["base_revid"] == 20


def test_pull_fetches_and_merges_only_primary_upstream(tmp_path, monkeypatch):
    # Verifies pull is primary fetch plus primary merge, unlike all-upstream fetch.
    _write_workspace(tmp_path)
    calls = _install_remotes(monkeypatch, _servers())

    assert run_pull(Namespace(root=tmp_path, path="Page.mw")) == 0

    assert ("primary", "fetch", 1) in calls
    assert not any(call[0] == "mirror" for call in calls)
    mapping = _config(tmp_path)["mappings"][0]
    assert mapping["upstreams"]["primary"]["base_revid"] == 11
    assert mapping["upstreams"]["mirror"]["base_revid"] == 20


def test_one_commit_can_push_to_each_tracked_upstream(tmp_path, monkeypatch):
    # Verifies pushing one staged body to one upstream does not consume it for the other.
    _write_workspace(tmp_path, local_text="shared edit\n")
    servers = _servers()
    servers["primary"].update({"revid": 10, "content": "primary base\n"})
    servers["mirror"].update({"revid": 20, "content": "mirror base\n"})
    _install_remotes(monkeypatch, servers)
    _credentials(monkeypatch)

    assert run_commit(_commit_args(tmp_path)) == 0
    assert run_push(_push_args(tmp_path, "mirror")) == 0
    after_mirror = _config(tmp_path)["mappings"][0]
    assert servers["mirror"]["content"] == "shared edit\n"
    assert after_mirror["upstreams"]["mirror"]["base_revid"] == 21
    assert after_mirror["upstreams"]["primary"]["base_revid"] == 10

    assert run_push(_push_args(tmp_path, "primary")) == 0
    assert servers["primary"]["content"] == "shared edit\n"
    after_both = _config(tmp_path)["mappings"][0]
    assert after_both["upstreams"]["primary"]["base_revid"] == 11


def test_push_creates_not_created_upstream_by_title(tmp_path, monkeypatch):
    # Verifies first push uses create-by-title and records the resulting page identity.
    _write_workspace(tmp_path, mirror_created=False, local_text="first mirror body\n")
    servers = _servers()
    servers["primary"].update({"revid": 10, "content": "primary base\n"})
    servers["mirror"] = {"pageid": None, "revid": 0, "title": "Page", "content": ""}
    calls = _install_remotes(monkeypatch, servers)
    _credentials(monkeypatch)

    assert run_commit(_commit_args(tmp_path)) == 0
    assert run_push(_push_args(tmp_path, "mirror")) == 0

    assert ("mirror", "create", "Page", "first mirror body\n", "mirror edit") in calls
    upstream = _config(tmp_path)["mappings"][0]["upstreams"]["mirror"]
    assert upstream["state"] == "tracked"
    assert upstream["pageid"] == 22
    assert upstream["base_revid"] == 1
    assert workspace.cached_body_path(tmp_path, "mirror", 22, 1).read_text() == "first mirror body\n"


def test_preview_defaults_to_primary_upstream(tmp_path, monkeypatch):
    # Verifies preview renders a migrated mapping through its primary wiki parser.
    _write_workspace(tmp_path, local_text="preview me\n")
    calls = _install_remotes(monkeypatch, _servers())
    args = Namespace(
        root=tmp_path,
        path="Page.mw",
        output="preview.html",
        open=False,
        link=True,
    )

    assert run_preview(args) == 0

    assert ("primary", "preview", "Page", "preview me\n") in calls
    assert "primary preview" in (tmp_path / "preview.html").read_text(encoding="utf-8")


def test_fsck_accepts_tracked_and_not_created_upstreams(tmp_path, capsys):
    # Verifies fsck checks tracked cache state while allowing a declared missing target.
    _write_workspace(tmp_path, mirror_created=False)

    assert run_fsck(Namespace(root=tmp_path)) == 0
    assert "fsck: ok" in capsys.readouterr().out
