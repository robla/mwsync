"""Tests for mwsync.py."""

import pytest
import mwsync


# This test exercises mwsync._parse_article_name to verify that it correctly:
# 1. Normalizes spaces and underscores (replacing '_' with ' ').
# 2. Strips leading/trailing whitespace.
# 3. Strips the '.mw' file extension from local filenames.
# 4. Raises SystemExit (exiting with code 1) for empty or whitespace-only inputs.
def test_parse_article_name():
    # Verify canonical name normalization works as expected:
    assert mwsync._parse_article_name("New York") == "New York"
    assert mwsync._parse_article_name("New_York") == "New York"
    assert mwsync._parse_article_name("New_York.mw") == "New York"
    assert mwsync._parse_article_name("  New_York.mw  ") == "New York"

    # Verify empty/whitespace inputs raise a SystemExit exception (exit 1):
    with pytest.raises(SystemExit) as excinfo:
        mwsync._parse_article_name("")
    assert excinfo.value.code == 1

    with pytest.raises(SystemExit) as excinfo:
        mwsync._parse_article_name("   ")
    assert excinfo.value.code == 1
