# Offline unit tests for the pure workspace pieces: upsert_page_mapping identity
# (insert vs. update keyed on (remote, pageid), not the movable title) and the
# pageid-keyed cache layout written by cache_page_rev (page.yaml marker,
# revid-named body/sidecar, and a title-bearing history.jsonl). No network.

import json

import yaml

from mwmap import workspace


def test_upsert_inserts_then_updates_same_pageid():
    config = workspace.initial_config()
    workspace.upsert_page_mapping(
        config, remote="r", pageid=1, title="A", local_path="A.mw", fmt="mw", base_revid=10
    )
    # Same pageid, page moved to a new title and advanced one revision.
    workspace.upsert_page_mapping(
        config, remote="r", pageid=1, title="A (moved)", local_path="A.mw", fmt="mw", base_revid=11
    )
    assert len(config["mappings"]) == 1
    mapping = config["mappings"][0]
    assert mapping["pageid"] == 1
    assert mapping["remote_path"] == "A (moved)"
    assert mapping["base_revid"] == 11


def test_upsert_distinguishes_pageids():
    config = workspace.initial_config()
    workspace.upsert_page_mapping(
        config, remote="r", pageid=1, title="A", local_path="A.mw", fmt="mw", base_revid=10
    )
    workspace.upsert_page_mapping(
        config, remote="r", pageid=2, title="B", local_path="B.mw", fmt="mw", base_revid=20
    )
    assert len(config["mappings"]) == 2


def test_cache_page_rev_writes_pageid_layout(tmp_path):
    metadata = {
        "pageid": 4242,
        "title": "California",
        "revid": 99,
        "parentid": 0,
        "timestamp": "2026-01-01T00:00:00Z",
        "contentmodel": "wikitext",
        "redirect": False,
    }
    workspace.cache_page_rev(tmp_path, "electowiki", "body text", metadata)

    page_dir = tmp_path / "_mwmap" / "cache" / "electowiki" / "4242"
    assert (page_dir / "99.mw").read_text() == "body text"
    assert (page_dir / "99.yaml").exists()

    info = yaml.safe_load((page_dir / "page.yaml").read_text())
    assert info == {"pageid": 4242, "title": "California", "remote": "electowiki"}

    record = json.loads((page_dir / "history.jsonl").read_text().strip())
    assert record["title"] == "California"
    assert record["revid"] == 99
    assert record["body"] == "99.mw"
