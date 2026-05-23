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

Since `mwsync.py` uses `urllib.request.urlopen` via helper functions, mock the low-level API call or the high-level `_api_get` / `_fetch_page` helpers. Mocking the high-level `_fetch_page` or `_api_get` is simpler and less fragile than mocking standard library socket/HTTP streams.

Example mocking `_fetch_page`:

```python
from unittest.mock import patch

@patch('mwsync._fetch_page')
def test_fetch_mocked(mock_fetch):
    mock_fetch.return_value = {
        "revid": 12345,
        "title": "Maine",
        "ns": 0,
        "pageid": 99,
        "wikitext": "Mock page content"
    }
    # Invoke fetch command logic...
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
- **Objective**: Verify the parser handles spaces, underscores, and `.mw` extensions correctly.
- **Action**: Test the function directly with inputs:
  - `"New York"` -> returns `("New_York", "New York", "New_York.mw")`
  - `"New_York.mw"` -> returns `("New_York", "New York", "New_York.mw")`
  - Empty string `""` -> raises `SystemExit` (or prints error).

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
- **Action**: Register an article `"Maine"`, mock `_fetch_page` to return a predefined revision, and run `fetch`.
- **Assertions**:
  - `_cache/Maine/history.jsonl` contains the revision record.
  - `_cache/Maine/refs/upstream` contains the correct revid.
  - `_cache/Maine/12345.mw` contains the wikitext body.
