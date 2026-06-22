# Offline unit tests for the pure text merge primitive used by `merge` and
# `pull`. These pin fast-forward, clean disjoint edit, and conflict behavior
# without involving workspace config, cache files, remotes, or the CLI.

from mwmap.core.textmerge import three_way_merge


def test_three_way_merge_takes_remote_when_local_unchanged():
    # Verifies an unchanged local copy fast-forwards to the remote text.
    merged, conflicted = three_way_merge("base\n", "base\n", "remote\n")

    assert merged == "remote\n"
    assert conflicted is False


def test_three_way_merge_combines_disjoint_edits():
    # Verifies independent local and remote edits are merged without markers.
    base = "top\nleft\nmiddle\nright\nbottom\n"
    mine = "top\nLOCAL\nmiddle\nright\nbottom\n"
    other = "top\nleft\nmiddle\nREMOTE\nbottom\n"

    merged, conflicted = three_way_merge(base, mine, other)

    assert merged == "top\nLOCAL\nmiddle\nREMOTE\nbottom\n"
    assert conflicted is False


def test_three_way_merge_marks_conflicting_edits():
    # Verifies competing edits to the same base region produce conflict markers.
    merged, conflicted = three_way_merge("a\nb\nc\n", "a\nLOCAL\nc\n", "a\nREMOTE\nc\n")

    assert conflicted is True
    assert merged == "a\n<<<<<<< local\nLOCAL\n=======\nREMOTE\n>>>>>>> remote\nc\n"
