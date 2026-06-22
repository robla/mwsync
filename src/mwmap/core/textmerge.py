"""Pure-Python line-based three-way merge (diff3-style).

Used by `merge`/`pull` to integrate an upstream MediaWiki revision into a
locally edited working file, against the common base revision. Kept dependency
-free on purpose: a wiki-sync tool should not require the `git` binary just to
merge text. Produces standard `<<<<<<< / ======= / >>>>>>>` conflict markers.
"""

from __future__ import annotations

import difflib


def _base_index_map(base_lines: list[str], other_lines: list[str]) -> dict[int, int]:
    """Map base line index -> other line index for lines that match unchanged."""
    mapping: dict[int, int] = {}
    matcher = difflib.SequenceMatcher(a=base_lines, b=other_lines, autojunk=False)
    for base_i, other_i, size in matcher.get_matching_blocks():
        for offset in range(size):
            mapping[base_i + offset] = other_i + offset
    return mapping


def three_way_merge(
    base: str,
    mine: str,
    other: str,
    mine_label: str = "local",
    other_label: str = "remote",
) -> tuple[str, bool]:
    """Merge `mine` and `other` against common ancestor `base`.

    Returns `(merged_text, conflicted)`. Where only one side changed a region,
    that side's text is taken; where both changed it identically, the shared
    change is taken; where both changed it differently, conflict markers are
    emitted and `conflicted` is True.
    """
    base_lines = base.splitlines(keepends=True)
    mine_lines = mine.splitlines(keepends=True)
    other_lines = other.splitlines(keepends=True)

    mine_map = _base_index_map(base_lines, mine_lines)
    other_map = _base_index_map(base_lines, other_lines)

    # Synchronization points: base lines unchanged in BOTH sides, in order.
    # SequenceMatcher blocks are monotonic, so sorting by base index keeps the
    # mine/other cursors monotonic too.
    sync_points = sorted(set(mine_map) & set(other_map))
    sync_points.append(len(base_lines))  # sentinel flushes the final chunk

    out: list[str] = []
    conflicted = False
    prev_o = prev_a = prev_b = 0

    for base_i in sync_points:
        mine_i = mine_map.get(base_i, len(mine_lines))
        other_i = other_map.get(base_i, len(other_lines))

        base_chunk = base_lines[prev_o:base_i]
        mine_chunk = mine_lines[prev_a:mine_i]
        other_chunk = other_lines[prev_b:other_i]

        if mine_chunk == other_chunk:
            out.extend(mine_chunk)
        elif mine_chunk == base_chunk:
            out.extend(other_chunk)
        elif other_chunk == base_chunk:
            out.extend(mine_chunk)
        else:
            conflicted = True
            out.append(_marker_line(f"<<<<<<< {mine_label}"))
            out.extend(mine_chunk)
            out.append(_marker_line("======="))
            out.extend(other_chunk)
            out.append(_marker_line(f">>>>>>> {other_label}"))

        if base_i < len(base_lines):
            out.append(base_lines[base_i])  # the shared sync line itself
        prev_o, prev_a, prev_b = base_i + 1, mine_i + 1, other_i + 1

    return "".join(out), conflicted


def _marker_line(text: str) -> str:
    """Return a conflict marker line with a trailing newline."""
    return f"{text}\n"


def has_conflict_markers(text: str) -> bool:
    """Return whether `text` still contains unresolved merge conflict markers.

    Matches all three diff3 marker forms, mirroring legacy mwsync's predicate
    (see docs/legacy-code-copy.md), not only the opening `<<<<<<< `.
    """
    return any(
        line.startswith(("<<<<<<< ", ">>>>>>> ")) or line == "======="
        for line in text.splitlines()
    )
