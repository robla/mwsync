#!/usr/bin/env python3
"""Thin source-checkout entry point for mwmap.

Typical call stack:
  mwmap.py -> src/mwmap/cli.py -> src/mwmap/commands/<verb>.py
"""

from __future__ import annotations

from pathlib import Path
import sys


SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

from mwmap.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
