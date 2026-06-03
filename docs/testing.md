# Testing

No test suite exists yet. This repository is currently focused on design and early architecture.

## Working Policy

Do not write tests concurrently with implementation unless explicitly requested. If existing tests are missing, incomplete, or not targeted enough to make a requested change confidently and safely, stop and ask before editing.

This is especially important for synchronization behavior, merge behavior, page identity handling, and anything involving MediaWiki revision state.

## Future Test Suite

If a test suite is introduced, prefer `pytest` unless the project adopts a different framework. Name tests by observable behavior, for example:

```text
test_status_reports_unpaired_pages.py
test_merge_preserves_page_identity.py
test_push_refuses_stale_revision.py
```

Unit tests should cover pure mapping and merge logic without network access. Tests that require MediaWiki credentials, local notebooks, or external services should be documented clearly and separated from fast local tests.
