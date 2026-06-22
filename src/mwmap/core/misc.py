"""Small shared helpers that do not own mwmap state."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import tempfile


def die(message: str) -> None:
    """Print a CLI error and exit nonzero."""
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write text by replacing the destination from a temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def remote_name_from_host(hostname: str) -> str:
    """Derive a simple remote name from a URL hostname."""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    name = hostname.split(".", 1)[0]
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "remote"
