# rcmgr.py Specification

`rcmgr.py` is the recent-changes companion tool for the MediaWiki instance
managed by the current `mwsync.yaml`. It builds and maintains a durable,
partitioned local cache of the target wiki's recent-changes feed. Instead of a
single persistent file, it partitions changes across daily files that accumulate
over time, so the cache covers the full stream from the first run forward.

Like `catmgr.py`, `rcmgr.py` is a wiki-level tool, not a per-article tool. It
reads `wiki.api_base` from `mwsync.yaml` and caches state under `_cache/` in the
same working directory. Each mwsync working directory corresponds to one target
wiki, so the recent-changes cache is scoped to that wiki.

## The Core Constraint

This spec exists because of one fact about MediaWiki: the `list=recentchanges`
API does not return all history. It serves only a bounded recent window governed
by the wiki's `$wgRCMaxAge` (commonly 30 or 90 days). Changes older than that
window are purged from the `recentchanges` table and are simply not available
from this API, even though the underlying revisions still exist.

The consequence shapes the whole tool:

- A single run can capture only the changes currently inside the wiki's RC
  window. It cannot reach back before the window, and on the very first run it
  cannot reach back before that window either.
- "All the way back to the first time I run this" is achievable only by
  **accumulating** across runs into a partitioned on-disk structure. Each run
  merges new changes into the corresponding daily partition files. Historical
  changes are durable: once collected they are preserved forever, even after they
  disappear from the wiki's transient `recentchanges` feed.
- If runs are spaced farther apart than the wiki's RC retention window,
  successive RC windows will not overlap and a permanent **gap** forms in local
  coverage. Frequent runs are the way to guarantee gapless coverage. The MVP does
  not detect or report such gaps (see Future Directions); the accumulated data
  stays correct, it may just be non-contiguous.

Reaching further back than the first run, or backfilling a gap, would require a
different API surface (per-page `prop=revisions`, `list=allrevisions`, or
`Special:Export`). That is out of scope.

## MVP Scope

The first version is deliberately minimal. It delivers the core promise —
accumulate recent changes across runs into durable storage that survives the RC
window — and nothing more.

**In the MVP:**

- Commands: `fetch`, `status`, and a plain-text `log`.
- Daily-partitioned JSONL storage under `_cache/_recent_changes/`.
- A small `manifest.json` with a `schema_version`, a watermark, and fetch
  timestamps.
- Incremental fetch from the watermark with a short overlap, deduped by `rcid`.
- The never-nuke format primitives (stable `rcid` keys, verbatim rows,
  `schema_version`).

**Deferred (see Future Directions):**

- Coverage tracking and gap detection (the `coverage` manifest field, a `gaps`
  command, and fetch-time gap warnings).
- An `fsck` consistency checker.
- A wikitext `log` formatter that approximates `Special:RecentChanges`.
- An `rcmgr.py migrate` command for `schema_version` bumps.

## Design Goals

- **Append-only and idempotent.** Re-fetching an overlapping window must never
  duplicate, reorder, or corrupt stored changes. Running `fetch` twice in a row
  is a no-op for already-captured changes.
- **Never need to nuke.** The on-disk format should be stable and
  forward-compatible enough that future caching-strategy changes are handled by
  migration rather than by discarding accumulated history. Nuking the cache
  should be an aspiration we never reach, not a routine recovery step.

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

Use `_cache/_recent_changes/` immediately for `rcmgr.py`, even though the
current implemented `catmgr.py` still uses `_cache/categories/`. This follows the
intended wiki-level cache naming convention rather than copying the older
category-cache path.

Treat `_cache/_recent_changes/` as refreshable runtime state by default, not as
state users are expected to commit to the project git repository. The cache
should still be deterministic and diff-friendly because users may inspect it
directly, but normal recovery should come from re-running `fetch`.

Changes are partitioned into daily JSONL files named `YYYY-MM-DD.jsonl` by the
UTC day of each change's `timestamp` (the first 10 characters of the timestamp).
Daily partitioning avoids week/month boundary math, keeps git diffs surgical
(usually only today's file changes during daily runs), and keeps individual files
small. Within a partition, one change is stored per line, sorted by
`(timestamp, rcid)`. When a fetch retrieves overlapping or older changes, they
are merged and deduped by `rcid` into their corresponding files, so files are
updated continuously while historical data stays intact.

Writes use the same discipline as the rest of the toolkit:

- Atomic writes (temp file + `os.replace()`), as in `mwsync.py`'s
  `_atomic_write()`.
- UTF-8, newline-terminated JSONL.
- Deterministic ordering so the files are reviewable as diffs.
- No authentication required; recent changes are public.

### Change Record Schema

Each line in a partition file is one recent-change record. Store each returned
API row verbatim after essential validation. Do not synthesize absent boolean or
list fields into explicit defaults; for example, if the API omits `minor`,
`bot`, `new`, or `tags`, the cached row should omit it too. Preserving the raw
fields (rather than a lossy projection) is what lets future strategy changes be
migrations instead of nukes.

```json
{"rcid":884512,"type":"edit","ns":0,"title":"Approval voting","pageid":143,"revid":19901,"old_revid":19900,"timestamp":"2026-05-31T18:22:04Z","user":"Example","userid":42,"comment":"fix typo","oldlen":10421,"newlen":10418,"sha1":"abc123","tags":[]}
```

- `rcid` is the stable unique key for a change. Dedup is by `rcid`.
- `type` is one of `edit`, `new`, `log`, `categorize`, `external`.
- For `type:"log"` entries, also store `logid`, `logtype`, `logaction`, and
  `logparams` as returned. Log entries may have `revid:0`; that is expected.
- Records missing `rcid`, `timestamp`, or `type` are malformed for this cache and
  should make `fetch` fail rather than storing a row that cannot be deduped,
  partitioned, or interpreted.
- Unknown / future API fields encountered during a fetch should be preserved
  rather than dropped, so the record can round-trip across schema revisions.

### Manifest

`manifest.json` records cache-wide metadata:

```json
{
  "schema_version": 1,
  "api_base": "https://electowiki.org/w/api.php",
  "first_fetch_at": "2026-05-15T00:00:00Z",
  "last_fetch_at": "2026-06-07T00:00:00Z",
  "watermark": {"timestamp": "2026-06-07T17:55:12Z", "rcid": 884901},
  "total_changes": 5123
}
```

- `schema_version` gates future migration. It is bumped only when the on-disk
  format changes in a way that older readers cannot handle.
- `watermark` is the newest `(timestamp, rcid)` captured so far and the logical
  starting point for the next incremental fetch. The actual API query overlaps a
  short interval before this timestamp (see Fetch Algorithm), but the watermark
  remains the newest committed change.
- `total_changes` is the count of unique changes stored across all partitions.

Coverage intervals and gap tracking are intentionally not part of the MVP
manifest; adding a `coverage` field later is a forward-compatible change that
older readers can ignore.

## Fetch Algorithm

`fetch` pulls everything new since the watermark and merges it into the store,
idempotently and transactionally.

API query (Action API, endpoint from `wiki.api_base`):

```text
action=query
list=recentchanges
rcdir=newer
rcstart=<watermark.timestamp minus 10 minutes>   # omitted on the first run
rclimit=max
rcprop=ids|title|timestamp|user|userid|comment|flags|sizes|loginfo|tags|sha1
rctype=edit|new|log|categorize
format=json
formatversion=2
```

Steps:

1. Read the manifest (or initialize an empty one on first run).
2. Query with a 10-minute overlap before the watermark (`rcdir=newer`,
   `rcstart=watermark.timestamp - 10 minutes`), following `continue` until
   exhausted. On the first run, omit `rcstart` so the query returns the wiki's
   entire available RC window (oldest-first). If `rcstart` predates the wiki's
   retention window the API simply returns from the oldest available change; no
   special handling is required in the MVP.
3. Buffer returned records into memory or a per-command staging area, not the
   live partition files. Validate that every record has at least `rcid`,
   `timestamp`, and `type`.
4. Build merged versions of the affected daily partitions in staging: load the
   existing live partition, index by `rcid`, add new records, re-sort by
   `(timestamp, rcid)`, and serialize deterministic JSONL. Existing records with
   the same `rcid` are left as they were (a change is immutable once it has
   happened), so overlapping windows dedup cleanly.
5. Recompute manifest fields: advance `watermark` to the newest committed
   `(timestamp, rcid)`, set `last_fetch_at`, set `first_fetch_at` if unset, and
   update `total_changes` by the number of newly added unique records.
6. Atomically replace affected live partitions, then write `manifest.json` last,
   atomically.

`manifest.json` is the authoritative commit marker. A failed `fetch` must not
advance the manifest watermark. The implementation should clean up staging files
on failure. If a failure occurs after one or more partition files were replaced
but before `manifest.json` was written, the next successful `fetch` re-merges the
same changes idempotently (dedup by `rcid`), so the only effect is harmless
extra rows that the next run reconciles.

If a fetch returns no records, update `last_fetch_at` and leave the watermark and
`total_changes` unchanged. On an empty first run, set `first_fetch_at` and
`last_fetch_at`, leave `watermark` unset, and set `total_changes` to `0`.

## Commands

```bash
rcmgr.py fetch
rcmgr.py status
rcmgr.py log
rcmgr.py log --since 2026-06-01 --ns 0 --type edit
rcmgr.py log Approval_voting
```

Meanings:

- `fetch`: pull changes since the watermark and merge them into the cache. With
  `--dry-run`, report what would be fetched (the query window and watermark)
  without writing.
- `status`: print `api_base`, first/last fetch times, watermark, and total change
  count. Report cache age, like `catmgr.py status`.
- `log`: print cached changes newest-first as plain text lines. Supports filters
  that read only local cache state:
  - `--since DATE` / `--until DATE`: restrict by timestamp. Date-only values
    such as `2026-06-01` are interpreted as UTC calendar days. `--since` is
    inclusive; `--until` is exclusive, and a date-only `--until 2026-06-01`
    means before `2026-06-02T00:00:00Z`.
  - `--ns N`: restrict by namespace.
  - `--type TYPE`: restrict by change type (`edit`, `new`, `log`, ...).
  - `--user NAME`: restrict by editor.
  - `--limit N`: cap output.
  - A positional article key/title filters to changes touching that page.

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
```

### Example `log` Output

Plain text, one change per line, newest first:

```text
2026-06-07T17:55:12Z  edit  Approval voting        Example  fix typo
2026-06-07T17:42:01Z  edit  Instant-runoff voting  Example  expand references
2026-06-07T17:20:33Z  log   Approval voting        Admin    protect
```

A wikitext-formatted `log` (diff/history links, grouped by date) is a Future
Direction; the MVP keeps the output simple and terminal-friendly.

## Never-Nuke Discipline

The user's stated aspiration is to never have to discard the local cache. The
MVP format supports that:

- **Stable keys.** Records are keyed by immutable `rcid`; a change never needs to
  be rewritten once captured, so re-fetching is always safe.
- **Verbatim fields.** Storing raw API fields (plus preserving unknown ones)
  means a richer future schema can be derived from existing records instead of
  re-downloading or discarding them.
- **Versioned format.** `schema_version` in the manifest lets a future
  `rcmgr.py migrate` rewrite partitions in place when the layout changes, rather
  than forcing a nuke. This matches the repository's stated preference for
  migration over compatibility shims.
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
- The recent-changes cache is informational for now. Wiring it into `mwsync.py`
  workflows (for example, "which tracked articles changed upstream recently?")
  is a Future Direction.

## Open Questions

These are non-blocking for a first implementation; an implementer may pick a
reasonable default and note it.

- **Suppressed / deleted revisions.** A revision can be hidden via
  RevisionDelete or oversight *after* `rcmgr.py` has cached it, leaving the
  original user, comment, or size in the local cache. Such changes also fall out
  of `recentchanges`, so the cache cannot notice the suppression on its own.
  Should a future version attempt to re-validate and scrub previously cached
  entries the wiki now reports as deleted, and by what mechanism?
- **Overlap sizing.** Is a fixed 10-minute overlap before the watermark always
  sufficient given client/server clock skew or a long-running fetch, or should
  the overlap be configurable or derived from the previous fetch's duration?

MVP implementation defaults: `fetch` refuses `api_base` drift with a clear error
because a working directory is dedicated to one wiki. Date-only `--since` and
`--until` values are accepted as UTC calendar-day boundaries; `--since` is
inclusive and `--until` is exclusive.

## Future Directions

These are split by priority. The **high-priority** items are the ones a real
consumer needs *now*: the monthly [[ElectoramaNews]] newsletter has an
"electowiki" section that is an editor-grouped activity summary built directly
from this cache, and the May 2026 edition exposed exactly what the tool is
missing to generate it. Everything under **lower priority** is durability,
recovery, and convenience work that the cache's never-nuke design already keeps
the door open for; none of it blocks the newsletter.

### High priority — generating the ElectoramaNews "electowiki" summary

The newsletter's electowiki section groups a month of changes by editor:

```text
* '''[[User:RobLa|RobLa]]''': [[Software]], [[Ohio]], … — 2026-05-01 – 2026-05-30, 105 edits, 24 new pages
* '''[[User:Kristomun|Kristomun]]''': [[Borda count]], … — 2026-05-05 – 2026-05-31, 8 edits, 2 new pages
…
'''May totals''': 8 editors made 130 edits across 65 pages (29 newly created).
New accounts registered in May: Carlo Estefano and FedeP.  Files uploaded: … (by RobLa).
```

(The per-editor line may also gain a distinct-page count, e.g. "8 edits to 7
pages, 2 new pages" — so the rollup must carry that count too.)

For the May edition this was produced **by hand with `jq` over the partition
JSONL**, because `log`'s plain text can't be parsed reliably and there is no
rollup. The pieces the tool needs to own this end-to-end:

1. **Machine-readable `log` output** — `log --format json|jsonl|tsv`. The
   plain-text columns can't be parsed when a comment contains padding or runs of
   spaces, so any generator has to bypass `log` and read the raw partitions. A
   structured emitter is the single biggest unlock and a prerequisite for the
   rest. (This is the data-format generalization of the wikitext-`log` future
   direction below: emit a data shape, not just a wiki-paste shape.)

2. **A `summary` rollup mode** — `rcmgr.py summary --since … --until …
   --group-by editor|page`, structured output, returning deduped aggregates
   instead of raw rows:
   - `--group-by editor`: per editor — distinct pages touched (titles + count),
     edit count, new-page count, and first/last edit timestamp *within the
     window*.
   - `--group-by page`: per page — distinct editors, created-vs-edited, first/
     last edit timestamp.
   - Window-level totals: distinct editors, total edits, distinct pages, new
     pages.

   Example `summary --group-by editor --format json` row:

   ```json
   {"user":"Kristomun","edits":8,"new_pages":2,"pages":["Borda count","InfMC","…"],
    "page_count":7,"first":"2026-05-05T…","last":"2026-05-31T…"}
   ```

3. **Multi-namespace selection in one call.** The summary spans main + user
   space (ns 0 and ns 2); `--ns` is currently single-valued. Allow repeated
   `--ns` (or `--ns 0,2`, or an "all namespaces" default) so one `summary` call
   covers the section instead of N calls stitched together.

4. **Noise controls, with edit-vs-log separation.** `--no-categorize` (the
   automatic category-membership rows — arguably default-off for `summary`) and
   `--no-bots` (the `bot` flag). Critically, a rollup's *edit* counts must count
   only `edit`/`new` rows; `categorize` and `log` rows must be excluded from
   per-editor edit tallies — even though `log` rows are needed for item 5.

5. **Log-derived participation stats.** The totals line needs two `type=log`
   aggregates surfaced *separately* from edit counts: `newusers` (account
   registrations → the "new accounts registered" list) and `upload` (files
   uploaded). Both are already cached as `type=log` with
   `logtype`/`logaction`; `summary` should expose their counts and affected
   users/titles so the totals line is generated, not hand-assembled.

**Explicitly *not* wanted: a "new editor" / first-edit flag.** An earlier draft
of the section starred editors whose first cached row fell in the window. It was
dropped because the cache only reaches back to its first run (the Core
Constraint), so "no rows before this month" cannot distinguish a genuinely new
editor from a long-time editor returning after a quiet stretch. We will not add a
`prop=revisions`/registration lookup just for this. The reliable new-editor
signal is the `newusers` log in item 5; that is the only newcomer signal the
summary should emit.

### Lower priority

- **Coverage tracking and gap detection.** A `coverage` field in the manifest
  recording the contiguous time intervals actually held, a `gaps` command, and a
  fetch-time warning when a run discovers the RC window has rolled past the
  previous watermark.
- **`fsck`.** A consistency checker that reports a missing or malformed manifest,
  partition contents that disagree with the manifest (`watermark`,
  `total_changes`), duplicate `rcid`s, out-of-order records, records filed in the
  wrong daily partition, and `api_base` drift. Detect only; pair with `migrate`
  for repair.
- **Wikitext `log` output** approximating `Special:RecentChanges` (grouped by
  date, with `Special:Diff` / history links) for pasting into a wiki page. (The
  high-priority structured `--format` emitter above is the more general need;
  this is the wiki-paste presentation of the same data.)
- **`rcmgr.py migrate`** to rewrite the store across `schema_version` bumps
  without nuking.
- **Redirect awareness in rollups.** To split new *redirects* from new *articles*
  in a new-page count, a record needs a redirect/content-type marker; the title
  cache does not currently supply one (its `redirect` field reads `false` across
  the board). Not needed for the current newsletter format, which counts new
  pages without distinguishing redirects.
- Backfilling coverage gaps and pre-first-run history from `prop=revisions` /
  `list=allrevisions` / `Special:Export`, the only sources that reach past the RC
  window.
- Hidden internal git storage for `_cache/_recent_changes/`, so the tool can
  recover from local runtime corruption without requiring users to commit
  refreshable cache files to the visible project repository.
- A `--watch` / polling mode that runs `fetch` on an interval.
- Integration with `mwsync.py status` to flag tracked articles with recent
  upstream activity.
- Optional `external` change-type capture if it proves useful. The MVP caches
  `edit|new|log|categorize`.
- Optional authenticated capture of `patrolled` / `autopatrolled` flags. The MVP
  does not request those fields because they require rights and can make
  anonymous fetches fail.
- Optional warning when the cache is older than a configurable threshold, as
  contemplated for `catmgr.py`.
