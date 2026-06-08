"""MVP test suite for rcmgr.py.

Six targeted tests covering the basic functionality of the recent-changes
cache: fetch writes the cache, fetch is idempotent and sorted/deduped across
runs, fetch refuses a wrong-wiki cache, status reports counts, log filters
records, and cache-less commands fail cleanly.

Network is never contacted: the high-level `rcmgr._fetch_recent_changes`
helper is mocked, mirroring the strategy in docs/testing.md. Each test runs in
an isolated temporary working directory.

Run with:  python3 -m pytest tests/test_rcmgr.py
"""

import argparse
import json
import os
from unittest.mock import patch

import pytest

import rcmgr

API = "https://electowiki.org/w/api.php"


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Run every test in a clean temp dir so _cache/ paths are isolated."""
    monkeypatch.chdir(tmp_path)


def _config(api_base=API):
    return {"wiki": {"api_base": api_base, "articles": {}}}


def _rows():
    """Three changes spanning two UTC days, oldest-first (rcdir=newer order)."""
    return [
        {"rcid": 101, "type": "edit", "ns": 0, "title": "Approval voting",
         "pageid": 1, "revid": 11, "old_revid": 10,
         "timestamp": "2026-05-01T10:00:00Z", "user": "Alice", "comment": "first"},
        {"rcid": 102, "type": "new", "ns": 1, "title": "Talk:Approval voting",
         "pageid": 2, "revid": 12,
         "timestamp": "2026-05-01T11:00:00Z", "user": "Bob", "comment": "talk"},
        {"rcid": 103, "type": "log", "ns": 6, "title": "File:Chart.png",
         "timestamp": "2026-05-02T09:00:00Z", "user": "Alice",
         "logtype": "upload", "logaction": "upload", "comment": ""},
    ]


def _fetch_args():
    return argparse.Namespace(dry_run=False)


def _log_args(article=None, since=None, until=None, namespace=None,
              change_type=None, user=None, limit=None):
    return argparse.Namespace(article=article, since=since, until=until,
                              namespace=namespace, change_type=change_type,
                              user=user, limit=limit)


def _run_fetch(rows, config=None):
    config = config or _config()
    with patch("rcmgr._fetch_recent_changes", return_value=rows):
        rcmgr.run_fetch(_fetch_args(), config)


def _manifest():
    with open(rcmgr.MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


# 1. fetch writes the manifest and daily-partitioned bodies ------------------
def test_fetch_writes_manifest_and_daily_partitions():
    _run_fetch(_rows())

    manifest = _manifest()
    assert manifest["schema_version"] == rcmgr.SCHEMA_VERSION
    assert manifest["api_base"] == API
    assert manifest["total_changes"] == 3
    assert manifest["watermark"] == {"timestamp": "2026-05-02T09:00:00Z", "rcid": 103}
    assert manifest["first_fetch_at"] and manifest["last_fetch_at"]

    # Changes are split into one file per UTC day.
    assert sorted(os.listdir(rcmgr.CHANGES_DIR)) == ["2026-05-01.jsonl", "2026-05-02.jsonl"]
    day1 = rcmgr._read_jsonl(rcmgr._partition_path("2026-05-01"))
    assert [r["rcid"] for r in day1] == [101, 102]


# 2. a second fetch is idempotent: deduped, sorted, watermark advances -------
def test_fetch_idempotent_sorted_and_deduped():
    _run_fetch(_rows())

    # Overlap re-sends rcid 103 and adds 104, delivered newest-first.
    overlap = [
        {"rcid": 104, "type": "edit", "ns": 0, "title": "Score voting",
         "pageid": 3, "revid": 13, "timestamp": "2026-05-02T12:00:00Z",
         "user": "Bob", "comment": "later"},
        _rows()[2],  # rcid 103 again
    ]
    _run_fetch(overlap)

    manifest = _manifest()
    assert manifest["total_changes"] == 4  # 103 not double-counted
    assert manifest["watermark"] == {"timestamp": "2026-05-02T12:00:00Z", "rcid": 104}

    day2 = rcmgr._read_jsonl(rcmgr._partition_path("2026-05-02"))
    assert [r["rcid"] for r in day2] == [103, 104]  # deduped and re-sorted


# 3. fetch refuses a cache built for a different wiki ------------------------
def test_fetch_refuses_api_base_drift():
    _run_fetch(_rows())

    drifted = _config("https://en.wikipedia.org/w/api.php")
    with patch("rcmgr._fetch_recent_changes", return_value=_rows()) as mock_fetch:
        with pytest.raises(SystemExit):
            rcmgr.run_fetch(_fetch_args(), drifted)
        mock_fetch.assert_not_called()  # drift is caught before any network call

    assert _manifest()["api_base"] == API  # cache untouched


# 4. status reports the cached api_base, watermark, and count ----------------
def test_status_reports_counts(capsys):
    _run_fetch(_rows())
    rcmgr.run_status(argparse.Namespace(), _config())

    out = capsys.readouterr().out
    assert API in out
    assert "rcid 103" in out
    assert "changes:      3" in out


# 5. log filters by type and limit, newest-first ----------------------------
def test_log_filters_and_orders(capsys):
    _run_fetch(_rows())

    # Type filter: only the single edit (rcid 101) should appear.
    rcmgr.run_log(_log_args(change_type="edit"), _config())
    out = capsys.readouterr().out
    assert "Approval voting" in out
    assert "File:Chart.png" not in out
    assert "Talk:Approval voting" not in out

    # No filter + limit: newest change comes first, capped to one line.
    rcmgr.run_log(_log_args(limit=1), _config())
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("2026-05-02T09:00:00Z")  # newest of the three


# 6. cache-less status and log fail cleanly ---------------------------------
def test_commands_require_cache(capsys):
    with pytest.raises(SystemExit):
        rcmgr.run_status(argparse.Namespace(), _config())
    with pytest.raises(SystemExit):
        rcmgr.run_log(_log_args(), _config())

    err = capsys.readouterr().err
    assert "Recent-changes cache not found" in err
