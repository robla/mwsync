"""Put `src/` on sys.path so unit tests can import the mwmap package directly.

The subprocess CLI tests run `mwmap.py` (which adds `src/` itself); these
in-process unit tests import `mwmap.*` and need the same path set up.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
