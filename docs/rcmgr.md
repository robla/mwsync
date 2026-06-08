# rcmgr.py Specification

`rcmgr.py` is the recent-changes companion tool for the MediaWiki instance
managed by the current `mwsync.yaml`. It builds and maintains a durable, partitioned
local cache of the target wiki's recent-changes feed. Instead of using a single persistent
file, it partitions changes across multiple daily files that accumulate over time, so
that the cache covers the full stream from the first run forward.

Like `catmgr.py`, `rcmgr.py` is a wiki-level tool, not a per-article tool. It
reads `wiki.api_base` from `mwsync.yaml` and caches state under `_cache/` in the
same working directory. Each mwsync working directory corresponds to one target
wiki, so the recent-changes cache is scoped to that wiki.

## The Core Constraint

This spec exists because of one fact about MediaWiki: the `list=recentchanges`
API does not return all history. It serves only a bounded recent window
governed by the wiki's `$wgRCMaxAge` (commonly 30 or 90 days). Changes older
than that window are purged from the `recentchanges` table and are simply not
available from this API, even though the underlying revisions still exist.

The consequence shapes the whole tool:

- A single run can capture only the changes currently inside the wiki's RC
  window. It cannot reach back before the window, and on the very first run it
  cannot reach back before that window either.
- "All the way back to the first time I run this" is achievable only by
  **accumulating** across runs into a partitioned on-disk structure. Each run merges
  new changes into the corresponding daily partition files. Historical changes are
  durable: they are collected and preserved in their respective files forever, even
  after they disappear from the remote wiki's transient `recentchanges` feed.
- If runs are spaced farther apart than the wiki's RC retention window,
  successive RC windows will not overlap and a permanent **gap** forms in local
  coverage. `rcmgr.py` cannot retroactively fill such a gap from
  `recentchanges`; it can only detect and record it. Frequent runs are the way
  to guarantee gapless coverage.

Reaching further back than the first run, or backfilling a detected gap, would
require a different API surface (per-page `prop=revisions`, `list=allrevisions`,
or `Special:Export`). That is explicitly out of scope for v0.01 and is listed
under Future Directions.

## Design Goals

- **Append-only and idempotent.** Re-fetching an overlapping window must never
  duplicate, reorder, or corrupt stored changes. Running `fetch` twice in a row
  is a no-op for already-captured changes.
- **Never need to nuke.** The on-disk format should be stable and
  forward-compatible enough that future caching-strategy changes are handled by
  migration rather than by discarding accumulated history. Nuking the cache
  should be an aspiration we never reach, not a routine recovery step.
- **Honest about coverage.** The cache should always be able to answer "what
  time ranges do I actually have?" and "where are the gaps?" rather than
  silently presenting partial data as complete.

## Cache Layout

Use a dedicated, underscore-prefixed wiki-level cache directory, consistent with
the `_cache/_categories/` / `_cache/_titles/` convention in
`docs/architecture-mwsync.md`:

```text
_cache/_recent_changes/
_cache/_recent_changes/manifest.json
_cache/_recent_changes/changes/2026-06-05.jsonl
_cache/_recent_changes/changes/2026-06-06.jsonl
```

Changes are partitioned into daily JSONL files named `YYYY-MM-DD.jsonl` by the
UTC day of each change's `timestamp` (the first 10 characters of the timestamp).
Daily partitioning is highly robust: it avoids complex week-boundary calculations,
keeps git diffs extremely surgical (usually only today's file is modified during
daily runs), and keeps individual files small. Within a partition file, one change
is stored per line, sorted by `(timestamp, rcid)`. When a fetch retrieves overlapping
or older changes, they are merged and deduped by `rcid` into their corresponding files,
allowing files to be updated continuously while keeping historical data intact.

Writes use the same discipline as the rest of the toolkit:

- Atomic writes (temp file + `os.replace()`), as in `mwsync.py`'s
  `_atomic_write()`.
- UTF-8, newline-terminated JSONL.
- Deterministic ordering so the files are reviewable as diffs.
- No authentication required; recent changes are public.

### Change Record Schema

Each line in a partition file is one recent-change record. Store the useful API
fields verbatim plus a small amount of normalization. Preserving the raw fields
(rather than a lossy projection) is what lets future strategy changes be
migrations instead of nukes.

```json
{"rcid":884512,"type":"edit","ns":0,"title":"Approval voting","pageid":143,"revid":19901,"old_revid":19900,"timestamp":"2026-05-31T18:22:04Z","user":"Example","userid":42,"comment":"fix typo","minor":false,"bot":false,"new":false,"oldlen":10421,"newlen":10418,"sha1":"abc123","tags":[]}
```

- `rcid` is the stable unique key for a change. Dedup is by `rcid`.
- `type` is one of `edit`, `new`, `log`, `categorize`, `external`.
- For `type:"log"` entries, also store `logid`, `logtype`, `logaction`, and
  `logparams` as returned. Log entries may have `revid:0`; that is expected.
- Unknown / future API fields encountered during a fetch should be preserved
  rather than dropped, so the record can round-trip across schema revisions.

### Manifest

`manifest.json` records cache-wide metadata and, critically, coverage:

```json
{
  "schema_version": 1,
  "api_base": "https://electowiki.org/w/api.php",
  "first_fetch_at": "2026-05-15T00:00:00Z",
  "last_fetch_at": "2026-06-07T00:00:00Z",
  "watermark": {"timestamp": "2026-06-07T17:55:12Z", "rcid": 884901},
  "coverage": [
    {"start": "2026-04-16T00:00:00Z", "end": "2026-05-20T12:00:00Z"},
    {"start": "2026-05-25T00:00:00Z", "end": "2026-06-07T17:55:12Z"}
  ],
  "total_changes": 5123
}
```

- `schema_version` gates migration. It is bumped only when the on-disk format
  changes in a way that older readers cannot handle.
- `watermark` is the newest `(timestamp, rcid)` captured so far. It is the
  starting point for the next incremental fetch.
- `coverage` is the list of contiguous time intervals the cache actually holds,
  oldest first, with gaps between intervals. A single interval means gapless
  coverage from `coverage[0].start` to the watermark. More than one interval
  means at least one gap. `coverage[0].start` is the earliest change the cache
  has ever seen; nothing before it is knowable from `recentchanges`.

## Fetch Algorithm

`fetch` pulls everything new since the watermark and merges it into the store,
idempotently and transactionally.

API query (Action API, endpoint from `wiki.api_base`):

```text
action=query
list=recentchanges
rcdir=newer
rcstart=<watermark.timestamp>      # omitted on the first run
rclimit=max
rcprop=ids|title|timestamp|user|userid|comment|flags|sizes|loginfo|tags|sha1
rctype=edit|new|log|categorize
format=json
formatversion=2
```

Steps:

1. Read the manifest (or initialize an empty one on first run).
2. Query from the watermark forward (`rcdir=newer`, `rcstart=watermark`),
   following `continue` until exhausted. On the first run, omit `rcstart` so the
   query returns the wiki's entire available RC window (oldest-first).
3. Buffer returned records into a per-command staging area, not the live
   partition files.
4. Merge into the affected daily partitions: load the existing partition,
   index by `rcid`, add new records, re-sort by `(timestamp, rcid)`, and write
   the partition atomically. Existing records with the same `rcid` are left as
   they were (a change is immutable once it has happened), so overlapping
   windows dedup cleanly.
5. Recompute manifest fields: advance `watermark`, set `last_fetch_at`, set
   `first_fetch_at` if unset, update `total_changes`, and update `coverage`
   (below).
6. Promote staged partitions into the live directory and write `manifest.json`
   last, atomically.

If any step fails before the final manifest write, remove the staging area and
leave the live cache and manifest untouched. A failed `fetch` must not advance
the watermark or leave partially merged partitions, mirroring the transactional
`fetch` discipline in `docs/architecture-mwsync.md`.

### Coverage and Gap Detection

After a successful fetch, compute the time range the fetch actually covered:
`[oldest_returned_timestamp, newest_returned_timestamp]` (for the first run,
`oldest_returned_timestamp` is the earliest change the wiki's RC window still
holds). Merge that interval into `coverage`:

- If it overlaps or is contiguous with the previous newest interval, extend that
  interval. This is the normal case for frequent runs.
- If the fetch's oldest returned change is **newer** than the previous
  watermark, the RC window has rolled past the previous watermark since the last
  run: a gap exists between the previous watermark and this fetch's oldest
  change. Record it as a new, separate coverage interval and surface it.

Gap detection is empirical rather than relying on `$wgRCMaxAge`, which the API
does not reliably expose: the cache infers the available window from the oldest
change actually returned. When a gap is detected, `fetch` prints a warning that
names the gap range and recommends running more frequently.

## Proposed Commands

```bash
rcmgr.py fetch
rcmgr.py status
rcmgr.py log
rcmgr.py log --since 2026-06-01 --ns 0 --type edit
rcmgr.py log Approval_voting
rcmgr.py gaps
rcmgr.py fsck
```

Meanings:

- `fetch`: pull changes since the watermark and merge them into the cache. With
  `--dry-run`, report what would be fetched (the query window and watermark)
  without writing.
- `status`: print `api_base`, first/last fetch times, watermark, total change
  count, coverage span, and the number of gaps. Report cache age, like
  `catmgr.py status`.
- `log`: print cached changes newest-first. Supports filters that read only
  local cache state:
  - `--since DATE` / `--until DATE`: restrict by timestamp.
  - `--ns N`: restrict by namespace.
  - `--type TYPE`: restrict by change type (`edit`, `new`, `log`, ...).
  - `--user NAME`: restrict by editor.
  - `--limit N`: cap output.
  - A positional article key/title filters to changes touching that page.
- `gaps`: list detected coverage gaps (the ranges between `coverage` intervals)
  and the unknowable range before `coverage[0].start`.
- `fsck`: check cache consistency without repairing. It reports a missing or
  malformed manifest, partition files whose contents disagree with the manifest
  (`total_changes`, `watermark`, `coverage`), duplicate `rcid`s, out-of-order
  records, records filed in the wrong daily partition, and `api_base` drift
  between the manifest and `mwsync.yaml`. Following the toolkit convention
  (`docs/migrate.md`, `docs/fsck.md`), `fsck` detects only; a future
  `rcmgr.py migrate` would repair.

If the cache is missing, commands other than `fetch` should fail with:

```text
Recent-changes cache not found. Run: rcmgr.py fetch
```

### Example `status` Output

```text
Recent-changes cache for https://electowiki.org/w/api.php
  first fetch:  2026-05-15T00:00:00Z
  last fetch:   2026-06-07T00:00:00Z (today)
  watermark:    2026-06-07T17:55:12Z (rcid 884901)
  changes:      5123
  coverage:     2026-04-16 .. 2026-06-07  (1 gap)
  run rcmgr.py gaps for details
```

## Never-Nuke Discipline

The user's stated aspiration is to never have to discard the local cache. The
spec supports that as follows:

- **Stable keys.** Records are keyed by immutable `rcid`; a change never needs to
  be rewritten once captured, so re-fetching is always safe.
- **Verbatim fields.** Storing raw API fields (plus preserving unknown ones)
  means a richer future schema can be derived from existing records instead of
  re-downloading or discarding them.
- **Versioned format.** `schema_version` in the manifest lets a future
  `rcmgr.py migrate` rewrite partitions in place when the layout changes, rather
  than forcing a nuke. This matches the repository's stated preference for
  migration over compatibility shims: `fsck` detects drift, `migrate` fixes it.
- **Append-only partitions.** Because partitions are sorted-and-merged rather
  than blindly appended, the store stays canonical and a migration can rebuild
  any single partition deterministically.

A true nuke (deleting `_cache/_recent_changes/`) should only ever be needed if
the format changes in a way no migration can bridge. The format above is chosen
specifically to avoid that.

## Relationship to Other Tools

- `rcmgr.py` owns `_cache/_recent_changes/` exclusively. It does not modify
  `mwsync.yaml`, article caches, or `catmap.yaml`.
- It shares `mwsync.py`'s config helpers and HTTP conventions. Like
  `ledecopy.py` and `catmgr.py`, it may import `load_config`, `get_api_base`,
  `_atomic_write`, and the `USER_AGENT` from `mwsync.py` rather than duplicating
  them.
- The recent-changes cache is informational for now. Wiring it into
  `mwsync.py` workflows (for example, "which tracked articles changed upstream
  recently?") is a Future Direction, not part of v0.01.

## Open Questions

- Should `categorize` and `external` change types be cached by default, or
  filtered out as noise behind an opt-in flag?
- Should `fetch` overlap the watermark by a small margin (re-querying the last
  few seconds) to be robust against same-second changes straddling a previous
  fetch boundary? Dedup by `rcid` makes this safe; the question is whether it is
  necessary.
- Should the cache store `patrolled` / `autopatrolled` flags, which require
  appropriate rights and may be absent for anonymous fetches?
- Should partition granularity be daily by default, or should there be an option
  to aggregate into weekly or monthly archives for low-traffic wikis? Daily is
  proposed as the most robust default because of its simplicity and small git diff footprint.
- Should recent-changes cache files be committed to git, or treated like other
  refreshable `_cache/` runtime state? (Same open question as `catmgr.md`.)

## Future Directions

- `rcmgr.py migrate` to rewrite the store across `schema_version` bumps without
  nuking.
- Backfilling coverage gaps and pre-first-run history from `prop=revisions` /
  `list=allrevisions` / `Special:Export`, the only sources that reach past the
  RC window.
- A `--watch` / polling mode that runs `fetch` on an interval.
- Integration with `mwsync.py status` to flag tracked articles with recent
  upstream activity.
- Per-user and per-page activity summaries derived from the cache.
- Optional warning when the cache is older than a configurable threshold, as
  contemplated for `catmgr.py`.
