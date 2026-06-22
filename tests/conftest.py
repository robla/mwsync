"""Put `src/` on sys.path so unit tests can import the mwmap package directly.

The subprocess CLI tests run `mwmap.py` (which adds `src/` itself); these
in-process unit tests import `mwmap.*` and need the same path set up.
"""

import socket
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Fail any test that opens a real network connection.

    The fast suite must never contact electowiki.org (or any production wiki);
    `clone`/`fetch` behavior is exercised with `urlopen` stubbed to canned JSON.
    This guard turns an accidental live request into a loud failure instead of a
    silent hit on a real wiki.

    Note: it patches sockets in the pytest process only, so the subprocess CLI
    tests (which spawn `mwmap.py`) are not covered — those run config-only verbs
    that never reach the network.
    """

    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "network access is blocked in the test suite; stub urlopen instead"
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
