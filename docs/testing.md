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
python3 -m pytest tests/test_fetch.py      # one file
python3 -m pytest -k parse_article_name    # by keyword
```

Pytest is the only test-time dependency; install it with `pip install pytest` (or via a virtualenv). No `pytest.ini` / `pyproject.toml` configuration is needed for the first pass — pytest's defaults discover `tests/test_*.py` automatically when invoked from the repo root.

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
