# Repository Merge Runbook

## Objective

Import `mwmap` under `mwmap/` in the `mwsync` repository without squashing or
rewriting either history. The combined work happens on an `mwmap` branch cut
from `mwsync/main`; the standalone repository remains recoverable.

## Rehearsal

The import was rehearsed on 2026-08-13 in a disposable clone using these source
tips:

- `mwsync/main`: `d606b5c0c533acaba6f4caca8ed393dffebaef00`
- `mwmap/main`: `8eae46b3887810abe324c758f4f15e6d91f619ee`

The rehearsal used the local `mwsync` checkout because it was 48 commits ahead
of `github/main`. Neither repository had tags. The real source tips must be
recorded again immediately before the actual import.

```bash
git clone --no-local /home/robla/src/mwsync /tmp/mwsync-import-rehearsal
cd /tmp/mwsync-import-rehearsal
git switch -c mwmap main
git remote add mwmap-source /home/robla/src/mwmap
git fetch mwmap-source main
git subtree add --prefix=mwmap mwmap-source main
```

Do not add `--squash`. In the rehearsal, the subtree merge had the original
`mwsync` and `mwmap` tips as parents, leaving all source commits reachable.

## Actual Import Checklist

1. Ensure both repositories have clean worktrees and record their final tips,
   branches, remotes, and tags.
2. Move aside the untracked `/home/robla/src/mwsync/mwmap` symlink. Do not alter
   the standalone repository it targets.
3. Re-run the rehearsed sequence in the real `mwsync` checkout on a new `mwmap`
   branch.
4. Keep the `mwmap-source` remote until all verification is complete.

## Verification

```bash
git rev-list mwmap-source/main --not HEAD
git merge-base --is-ancestor <recorded-mwmap-tip> HEAD
git show <recorded-mwmap-tip>:tasks.org
git log --graph --all --oneline
python3 -m pytest -q
python3 -m pytest -q mwmap/tests
```

The first command must print nothing and the second must exit successfully.
The rehearsal produced 13 passing legacy tests. The imported suite produced 38
passes and 11 intentional failures specifying `t0002` work. Combined-root test
discovery initially ran only the legacy suite; the combined branch now includes
both `tests` and `mwmap/tests` in root discovery.

## Actual Import

The actual import completed on 2026-08-13. Commit `8c0e93b` has the original
`mwsync` tip `d606b5c` and exact `mwmap` tip `7a52ade` as its parents. The
ancestry and omitted-commit checks passed, and the branch was published as
`github/mwmap` before the primary checkout switched to track it.
