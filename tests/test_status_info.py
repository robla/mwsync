"""Tests for the local status and info reports."""

import json
from types import SimpleNamespace

import mwsync


def _article_config():
    return {
        "wiki": {
            "api_base": "https://electowiki.org/w/api.php",
            "articles": {
                "Maine": {"title": "Maine", "local": "Maine.mw"},
            },
        },
    }


def _write_revision_state(tmp_path, *, local_text="base\n", upstream=124):
    cache = tmp_path / "_cache" / "Maine"
    (cache / "refs").mkdir(parents=True)
    (cache / "123.mw").write_text("base\n", encoding="utf-8")
    (tmp_path / "Maine.mw").write_text(local_text, encoding="utf-8")
    mwsync._write_ref("Maine", "base", 123)
    mwsync._write_ref("Maine", "upstream", upstream)
    mwsync._write_ref("Maine", "last-pushed", 123)


def test_info_json_reports_revision_provenance(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_revision_state(tmp_path)

    mwsync.run_info(SimpleNamespace(article="Maine", json=True),
                    _article_config(), "mwsync.yaml")

    result = json.loads(capsys.readouterr().out)
    assert result["remote_url"] == "https://electowiki.org/wiki/Maine"
    assert result["working_state"] == "clean"
    assert result["sync_state"] == "behind"
    assert result["working_revision"] == 123
    assert result["base_revision"] == 123
    assert result["upstream_revision"] == 124
    assert result["last_pushed_revision"] == 123
    assert result["pending_commit_base"] is None


def test_status_verbose_reports_modified_working_revision(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_revision_state(tmp_path, local_text="local edit\n")

    mwsync.run_status(SimpleNamespace(article="Maine", verbose=True),
                      _article_config(), "mwsync.yaml")

    output = capsys.readouterr().out
    assert "remote URL:        https://electowiki.org/wiki/Maine" in output
    assert "working revision:  uncommitted (based on r123)" in output
    assert "base revision:     r123" in output
    assert "upstream revision: r124" in output


def test_status_compact_reports_pending_commit_base(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_revision_state(tmp_path)
    mwsync._write_pending_commit(
        "Maine",
        {"base_revid": 123, "summary": "Update Maine", "create_new": False},
        "local edit\n",
    )

    mwsync.run_status(SimpleNamespace(article=None, verbose=False),
                      _article_config(), "mwsync.yaml")

    output = capsys.readouterr().out
    assert "pending" in output
    assert "based on r123; ready to push" in output
