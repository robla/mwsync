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


def test_help_lists_first_version_commands():
    result = run_mwmap("--help")

    assert result.returncode == 0, result.stderr
    help_text = result.stdout.lower()
    assert "usage:" in help_text
    assert "init" in help_text
    assert "source" in help_text
    assert "status" in help_text


def test_init_creates_empty_workspace_config(tmp_path):
    result = run_mwmap("--root", str(tmp_path), "init")

    assert result.returncode == 0, result.stderr
    config_path = tmp_path / "_mwmap" / "config.yaml"
    assert config_path.exists()
    assert (tmp_path / "_mwmap" / "cache").is_dir()
    assert yaml.safe_load(config_path.read_text()) == {
        "version": 1,
        "sources": {},
        "mappings": [],
    }
    assert "initialized" in result.stdout.lower()


def test_source_add_and_status_report_configured_source(tmp_path):
    init_result = run_mwmap("--root", str(tmp_path), "init")
    assert init_result.returncode == 0, init_result.stderr

    add_result = run_mwmap(
        "--root",
        str(tmp_path),
        "source",
        "add",
        "electowiki",
        "mediawiki",
        "https://electowiki.org/w/",
    )

    assert add_result.returncode == 0, add_result.stderr
    config = yaml.safe_load((tmp_path / "_mwmap" / "config.yaml").read_text())
    assert config["sources"]["electowiki"] == {
        "type": "mediawiki",
        "location": "https://electowiki.org/w/",
    }

    status_result = run_mwmap("--root", str(tmp_path), "status")
    assert status_result.returncode == 0, status_result.stderr
    status_text = status_result.stdout.lower()
    assert "electowiki" in status_text
    assert "mediawiki" in status_text
    assert "0 mappings" in status_text
