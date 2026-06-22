# Testing

The current test suite is a small `pytest` suite that covers the local first-version `mwmap.py` CLI behavior.

## Working Policy

Do not write tests concurrently with implementation unless explicitly requested. If existing tests are missing, incomplete, or not targeted enough to make a requested change confidently and safely, stop and ask before editing.

This is especially important for synchronization behavior, merge behavior, page identity handling, and anything involving MediaWiki revision state.

## Running Tests

Run the current tests with:

```sh
python3 -m pytest -q
```

The current tests use PyYAML to inspect `_mwmap/config.yaml`.

## Future Test Suite

Prefer `pytest` unless the project adopts a different framework. Name tests by observable behavior, for example:

```text
test_status_reports_unpaired_pages.py
test_merge_preserves_page_identity.py
test_push_refuses_stale_revision.py
```

Every test should start with a short comment, under 500 characters, describing what the test hopes to accomplish.

Unit tests should cover pure mapping and merge logic without network access. Tests that require MediaWiki credentials, local notebooks, or external services should be documented clearly and separated from fast local tests.

`clone` contacts MediaWiki, so its end-to-end behavior belongs in a separated integration group. The current fast suite stays offline; do not add a live-network `clone` test to it.
