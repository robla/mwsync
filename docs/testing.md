# Pytest Suite Design for mwsync.py

This document outlines the testing strategy, tools, and a minimal set of first-pass tests for implementing a `pytest`-based test suite in the `mwsync` repository.

## Testing Strategy and Tools

To keep the repository lightweight and aligned with standard Python workflows, the test suite will use:

- **pytest**: The primary test runner and framework.
- **unittest.mock**: Standard library mocking framework to mock network requests and system commands without external dependencies.
- **tmp_path (built-in fixture)**: To isolate the filesystem. Every test that reads/writes `mwsync.yaml` or `_cache/` must run in a clean, temporary directory.

### Isolation Rules
1. **No Live Network Requests**: All tests must mock the MediaWiki Action API.
2. **No Side Effects**: Tests must not read or modify the active workspace files. Use `tmp_path` and patch the working directory references if needed, or change the current working directory during the test execution.

### Layout and Invocation

Tests live under `tests/` at the repo root, named `test_*.py`. Run the suite with:

```bash
python3 -m pytest                          # full suite
python3 -m pytest tests/test_rcmgr.py      # one file
python3 -m pytest -k idempotent            # by keyword
```

Pytest is the only test-time dependency; install it with `pip install pytest` (or via a virtualenv).

A `pytest.ini` at the repo root is **required**, not optional:

```ini
[pytest]
testpaths = tests
pythonpath = .
```

It does two things, both necessary:

- **`testpaths = tests`** pins collection to `tests/`. A bare `pytest` then
  collects only that directory and never walks the rest of the checkout. This
  matters because a working copy may contain symlinks or large unrelated
  directories at its root; without this, collection can descend into them and
  hang. Contributors are free to keep such symlinks locally — pinning collection
  keeps them harmless rather than enumerating any specific names here.
- **`pythonpath = .`** puts the repo root on `sys.path` so tests can
  `import rcmgr` / `import mwsync`. The `pytest` console script does not add the
  current directory to `sys.path`; only `python -m pytest` does. Without this,
  `pytest` fails at collection with `ModuleNotFoundError`.

Do not delete `pytest.ini` thinking pytest's defaults suffice — they do not in
this repo.

If you explicitly collect the whole tree (for example `pytest .`) you opt out of
`testpaths`, so a checkout with root-level symlinks to large trees can be slow;
prefer the bare `pytest` or name a path under `tests/`.

---

## Mocking Techniques

### Filesystem Isolation with `tmp_path`

Use a fixture to automatically run tests in a temporary directory:

```python
import os
import pytest

@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    # Change current working directory to the temporary path
    monkeypatch.chdir(tmp_path)
```

### Mocking MediaWiki API Responses

Since `mwsync.py` uses `urllib.request.urlopen` via helper functions, mock the high-level `_fetch_page` helper instead of the standard library socket/HTTP stream — it is far less fragile, and the rest of the fetch pipeline reads from its return dict.

The return shape of `_fetch_page` is documented in its docstring; the fields the fetch pipeline actually consumes are `revid`, `wikitext`, `timestamp`, `sha1`, `size`, `parentid`, `user`, `comment`, `contentmodel`, `contentformat`. Mocks should populate at least `revid`, `wikitext`, `timestamp`, and `sha1` — missing fields trip up history-ledger writes.

```python
from unittest.mock import patch

@patch('mwsync._fetch_page')
def test_fetch_mocked(mock_fetch):
    mock_fetch.return_value = {
        "revid": 12345,
        "wikitext": "Mock page content",
        "timestamp": "2026-05-22T00:00:00Z",
        "sha1": "0" * 40,
        "size": 17,
        "parentid": 0,
        "user": "Test",
        "comment": "",
        "contentmodel": "wikitext",
        "contentformat": "text/x-wiki",
    }
    # Invoke fetch command logic with args.depth = 1 (see test 4 below).
```

---

## Minimal First-Pass Test List

> **Status:** the four `mwsync.py` tests below are still a *plan*, not yet
> implemented. The only suite currently in the repo is `tests/test_rcmgr.py`
> (see "Current Coverage and Gaps"). Build these out next; they remain the
> intended first pass for `mwsync.py` itself.

To establish the suite, the first pass will implement only four highly targeted tests covering basic CLI lifecycle, name parsing, config registration, and cache writing.

### 1. `test_init_creates_config`
- **Subcommand**: `init`
- **Objective**: Verify that initializing a new directory works and creates a correct default config structure.
- **Action**: Run the parser/main function with `['init']` or call `run_init` directly.
- **Assertions**:
  - `mwsync.yaml` file exists.
  - The content is valid YAML.
  - `wiki.api_base` matches the default value.
  - `wiki.articles` is empty.

### 2. `test_parse_article_name`
- **Unit Function**: `_parse_article_name(name)`
- **Objective**: Verify the parser strips the `.mw` suffix, normalizes underscores to spaces, and rejects empty input.
- **Action**: Test the function directly with inputs:
  - `"New York"` -> returns `"New York"`.
  - `"New_York"` -> returns `"New York"`.
  - `"New_York.mw"` -> returns `"New York"`.
  - Empty string `""` -> raises `SystemExit`.
- **Note**: `_parse_article_name` returns the raw canonical title (string), not a `(key, title, local)` tuple. Key and local-path derivation now live in `_article_fields_from_title`, which is exercised by test 3.

### 3. `test_register_article_target`
- **Unit Function**: `_register_article_target(config, config_path, target)`
- **Objective**: Verify adding an article correctly updates the configuration dictionary and saves it.
- **Action**: Load a minimal config, call `_register_article_target` with `"Maine"`, and check side effects.
- **Assertions**:
  - Returns `("Maine", art_dict, True)`.
  - Config file `mwsync.yaml` is updated with `"Maine"` in `wiki.articles`.
  - The entry contains the correct default `title`, `url`, and `local` fields.

### 4. `test_fetch_updates_cache`
- **Subcommand**: `fetch`
- **Objective**: Verify a successful mock API response writes all required Git-like cache artifacts.
- **Action**: Register an article `"Maine"`, mock `_fetch_page` to return a predefined revision (see shape above), and call `run_fetch` with `argparse.Namespace(article="Maine", dry_run=False, depth=1, all_known=False, with_bodies=False, quiet=True)`.
- **Assertions**:
  - `_cache/Maine/history.jsonl` contains the revision record.
  - `_cache/Maine/refs/upstream` contains the correct revid.
  - `_cache/Maine/12345.mw` contains the wikitext body.
- **Note**: `DEFAULT_HISTORY_DEPTH` is `50`, so the default fetch path also calls `_fetch_revision_metadata` for the older revision tail. Setting `depth=1` keeps the test mocking surface to a single function. If the suite later grows tests that exercise the multi-revision path, mock `_fetch_revision_metadata` and `_fetch_revision_by_revid` alongside `_fetch_page`.

---

## Current Coverage and Gaps

This section is the honest state of the suite, so contributors know what is
actually guarded versus what merely looks covered.

### What is tested

- **`tests/test_rcmgr.py`** — six MVP tests for `rcmgr.py`, mocking the
  high-level `rcmgr._fetch_recent_changes` helper (no live network):
  1. `fetch` writes `manifest.json` and daily-partitioned change files.
  2. A second `fetch` is idempotent — deduped by `rcid`, re-sorted, watermark
     advances.
  3. `fetch` refuses a cache built for a different `api_base`, before any
     network call.
  4. `status` reports api_base, watermark, and change count.
  5. `log` filters by `--type`/`--limit` and orders newest-first.
  6. `status`/`log` fail cleanly when the cache is missing.

That is the entire automated suite at present.

### Where coverage is lacking

**`rcmgr.py` (partial).** The six tests assert observable results, not the full
behavior surface. Not yet covered:

- `fetch --dry-run` output.
- Empty fetches: the first-run-empty manifest (no watermark, `total_changes: 0`)
  and the later-empty path that only bumps `last_fetch_at`.
- API pagination (`continue`) and the `_api_get` error branch — both are
  bypassed by mocking `_fetch_recent_changes`. A lower-level test that mocks the
  HTTP layer would exercise continuation and error handling.
- Record validation: a row missing `rcid`/`timestamp`/`type` should make
  `fetch` fail without writing.
- `_load_manifest` rejecting an unknown `schema_version`, and malformed
  JSON/JSONL handling.
- The remaining `log` filters (`--since`/`--until` boundary parsing including
  date-only vs full-timestamp and inclusive/exclusive edges, `--ns`, `--user`)
  and the positional article filter that resolves through `wiki.articles`.
- Log entry comment fallback to `logaction`.

**`mwsync.py` (none).** The main tool has no automated tests yet. The four-test
plan above is the intended starting point; until it lands, the entire
fetch/merge/commit/push/restore/diff/log/show/status/fsck surface is unguarded.
Highest-value targets beyond the planned four: `merge` (clean, fast-forward, and
conflict paths), `commit`/`push` base-revid handling, and revision-expression
resolution (`@upstream`, `@upstream^`, `@<revid>`).

**`ledecopy.py` (none).** No tests for lede extraction, the top-of-page template
stripper, attribution/category emission, or the pre-flight refusals.

**`catmgr.py` (none).** No tests for the category cache, `catmap.yaml`
resolution, or redirect substitution.

### Cross-cutting gaps

- No test asserts the `_atomic_write` transactional discipline (no partial cache
  state visible after a simulated mid-write failure).
- No coverage measurement is wired up. Consider `pytest --cov` once the suite is
  large enough to make a coverage number meaningful.
