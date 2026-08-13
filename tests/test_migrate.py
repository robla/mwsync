import subprocess
import sys
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MWMAP = PROJECT_ROOT / "mwmap.py"


def run_mwmap(*args):
    return subprocess.run(
        [sys.executable, str(MWMAP), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def _legacy_mapping(remote, pageid, title, local_path, base_revid):
    return {
        "type": "page",
        "remote": remote,
        "pageid": pageid,
        "format": "mw",
        "remote_path": title,
        "local_path": local_path,
        "base_revid": base_revid,
    }


def _workspace_config():
    return {
        "remotes": {
            "electowiki": {
                "type": "mediawiki",
                "location": "https://electowiki.org/w/",
            }
        },
        "mappings": [
            _legacy_mapping("electowiki", 2598, "California", "California.mw", 16692),
            _legacy_mapping("electowiki", 605, "User:RobLa", "02ns_User/RobLa.mw", 19447),
        ],
    }


def _write_config(root, config):
    metadata_dir = root / "_mwmap"
    metadata_dir.mkdir()
    (metadata_dir / "mwmap.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )


def _read_config(root):
    return yaml.safe_load((root / "_mwmap" / "mwmap.yaml").read_text(encoding="utf-8"))


def _multi_upstream_mapping(legacy):
    remote = legacy["remote"]
    return {
        "type": "page",
        "local_path": legacy["local_path"],
        "format": legacy["format"],
        "primary_upstream": remote,
        "upstreams": {
            remote: {
                "remote": remote,
                "pageid": legacy["pageid"],
                "remote_path": legacy["remote_path"],
                "base_revid": legacy["base_revid"],
                "state": "tracked",
            }
        },
    }


def test_migrate_selected_article_only(tmp_path):
    # Migrating by path upgrades exactly that article and leaves every other mapping unchanged.
    original = _workspace_config()
    _write_config(tmp_path, original)

    result = run_mwmap("--root", str(tmp_path), "migrate", "California.mw")

    assert result.returncode == 0, result.stderr
    migrated = _read_config(tmp_path)
    assert migrated["mappings"] == [
        _multi_upstream_mapping(original["mappings"][0]),
        original["mappings"][1],
    ]


def test_migrate_all_articles_requires_all_flag(tmp_path):
    # The explicit --all mode upgrades every legacy article in one operation.
    original = _workspace_config()
    _write_config(tmp_path, original)

    result = run_mwmap("--root", str(tmp_path), "migrate", "--all")

    assert result.returncode == 0, result.stderr
    migrated = _read_config(tmp_path)
    assert migrated["mappings"] == [
        _multi_upstream_mapping(mapping) for mapping in original["mappings"]
    ]


def test_migrate_without_path_refuses_multiple_articles(tmp_path):
    # Bare migrate must not guess among several articles or silently perform a bulk migration.
    original = _workspace_config()
    _write_config(tmp_path, original)

    result = run_mwmap("--root", str(tmp_path), "migrate")

    assert result.returncode != 0
    output = f"{result.stdout}\n{result.stderr}"
    assert "California.mw" in output
    assert "02ns_User/RobLa.mw" in output
    assert "--all" in output
    assert _read_config(tmp_path) == original


def test_migrate_preserves_mwsync_workspace_and_legacy_mwmap_config(tmp_path):
    # Verifies migration changes only mwmap's new config, preserving shared files and mwsync state.
    original = _workspace_config()
    metadata_dir = tmp_path / "_mwmap"
    metadata_dir.mkdir()
    legacy_config = metadata_dir / "config.yaml"
    legacy_config.write_text(yaml.safe_dump(original, sort_keys=False), encoding="utf-8")
    (tmp_path / "mwsync.yaml").write_text("articles:\n  California: {}\n", encoding="utf-8")
    old_cache = tmp_path / "_cache" / "California"
    old_cache.mkdir(parents=True)
    (old_cache / "16692.mw").write_text("old mwsync cache\n", encoding="utf-8")
    (tmp_path / "California.mw").write_text("working text\n", encoding="utf-8")
    before = {
        path: path.read_bytes()
        for path in (
            legacy_config,
            tmp_path / "mwsync.yaml",
            old_cache / "16692.mw",
            tmp_path / "California.mw",
        )
    }

    result = run_mwmap("--root", str(tmp_path), "migrate", "California.mw")

    assert result.returncode == 0, result.stderr
    assert all(path.read_bytes() == content for path, content in before.items())
    migrated = _read_config(tmp_path)["mappings"]
    assert migrated[0] == _multi_upstream_mapping(original["mappings"][0])
    assert migrated[1] == original["mappings"][1]
