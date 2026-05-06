# mwsync.py Roadmap

This roadmap assumes a clean break from the current `_cache/server--<Article_Key>.mw` layout once the next design is ready. The preferred direction is a flat-file cache with one directory per article and revision files named by MediaWiki `revid`.

## Target Cache Layout

Each article gets one cache directory:

```text
_cache/New_York/history.jsonl
_cache/New_York/19778.mw
_cache/New_York/19778.json
_cache/New_York/19791.mw
_cache/New_York/19791.json
```

The local editable working file remains outside `_cache`:

```text
New_York.mw
```

There is no separate `latest` file. The latest known upstream revision is inferred from the final valid entry in chronological `history.jsonl`, and `mwsync.yaml` may continue to store `upstream_revid` as convenient user-facing state.

## History Manifest

`history.jsonl` is the article's ordered revision ledger. It should be chronological from oldest known revision to newest known revision. Each line is one JSON object.

Example:

```json
{"revid":19778,"parentid":19720,"timestamp":"2026-03-18T05:04:08Z","user":"RobLa","comment":"Adding Newburgh section","sha1":"bbdf5e...","size":4201,"body":"19778.mw","meta":"19778.json"}
```

The manifest should be robust to partial history. It may start with the latest fetched revision, then later be extended backward or forward as more history is requested. If history is backfilled out of order, the write step should rewrite `history.jsonl` into chronological order rather than preserve fetch order.

## Revision Files

Revision body files are named by `revid`:

```text
_cache/New_York/19778.mw
```

Metadata sidecars use the same stem:

```text
_cache/New_York/19778.json
```

The `.mw` file contains only wikitext. The `.json` file stores API metadata and cache bookkeeping: page title, article key, URL, revid, parentid, timestamp, user, comment, sha1, size, contentmodel, contentformat, fetched_at, and any visibility flags.

This deliberately favors readability over storage efficiency. A small number of cached articles can afford plain text revision bodies.

## Mostly Immutable, Not Sacred

MediaWiki revisions should be treated as mostly immutable, but `mwsync.py` should not assume cached files are permanently correct just because the filename is a revid.

Expected behavior:

- If a revid body file does not exist, write it atomically.
- If it exists and the remote `sha1` matches cached metadata, reuse it.
- If it exists and metadata differs, do not silently overwrite it.
- On mismatch, write the newly fetched body to a conflict filename such as `19778.refetch-20260505T120000.mw` and report the discrepancy.
- Keep the canonical `19778.mw` stable until the user runs an explicit repair or refresh command.

This model optimizes for the normal case while still handling revision deletion, suppression, migration quirks, or earlier buggy cache writes.

## Git-Like Command Direction

The command vocabulary should make `fetch` the normal remote-update operation, similar to git.

Possible commands:

```bash
mwsync.py fetch New_York
mwsync.py fetch --depth 50 New_York
mwsync.py fetch --all-known New_York
mwsync.py log New_York
mwsync.py show New_York@upstream
mwsync.py show New_York@upstream^
mwsync.py diff New_York@upstream^ New_York@upstream
mwsync.py diff New_York@upstream New_York.mw
mwsync.py checkout New_York@upstream~5 --to scratch/New_York-old.mw
mwsync.py show New_York@19778
mwsync.py fsck New_York
```

`fetch New_York` should fetch the latest revision body and update `history.jsonl`. `fetch --depth N New_York` should fetch the latest revision body plus metadata for the newest N revisions. It should not fetch every body in that depth window unless another explicit option is added later. `--all-known` can backfill all available metadata, but should be explicit.

## Addressing Revisions

Use git-like revision expressions where they fit, but optimize for common mwsync use rather than copying every git rule.

- `New_York@upstream`: latest upstream revision in chronological `history.jsonl`
- `New_York@upstream^`: previous known upstream revision
- `New_York@upstream~5`: fifth ancestor before the latest known upstream revision
- `New_York@19778`: cached or fetchable revision by globally unique revid
- `New_York.mw`: local working file

Git uses `HEAD`, `HEAD^`, and `HEAD~N` for relative commit navigation. It also uses `branch@{upstream}` and `@{u}` for a branch's configured upstream. For `mwsync.py`, `New_York@upstream` is intentionally simpler than git's brace syntax because the article name already scopes the expression, and the latest upstream wiki revision is the default thing users will compare against.

Raw `revid` lookup should remain available, but it should be an escape hatch rather than the normal spelling users need for day-to-day work.

This keeps the cache layout simple. Symbolic names can be computed from `history.jsonl` and `mwsync.yaml` rather than stored as extra mutable files.

## Fetch Depth Semantics

Adopt metadata-only depth by default:

```bash
mwsync.py fetch --depth 50 New_York
```

This records metadata for the newest 50 revisions in chronological `history.jsonl`, but only guarantees that the latest revision body exists as `<revid>.mw`.

Pros: API- and disk-light, good for `log`, and consistent with the idea that normal fetches should not pull the full page history.

Cons: `show New_York@upstream^` or `diff New_York@upstream^ New_York@upstream` may need a later network fetch if the requested old body is not cached.

If bulk body caching is needed later, add an explicit option:

```bash
mwsync.py fetch --depth 50 --with-bodies New_York
```

That keeps the default lightweight while preserving a clear path for offline archival use.

## Recommended Next Step

Implement the per-article cache layout first:

1. Add helpers for `_cache/<Article_Key>/`, `history.jsonl`, `<revid>.mw`, and `<revid>.json`.
2. Change `fetch` to write the latest fetched revision body under `_cache/<Article_Key>/<revid>.mw`.
3. Append or merge the latest revision metadata into chronological `history.jsonl`.
4. Change `diff` to compare `New_York@upstream` against `New_York.mw`.
5. Add `log` and `show` once the manifest is stable.

Avoid compatibility code for `_cache/server--<Article_Key>.mw` unless an immediate personal migration needs it. The current user base is small enough that a clean cache reset is acceptable.

## Core Invariants

- `history.jsonl` is chronological.
- The latest upstream revision is inferred from the last valid manifest entry.
- Revid-named body files are stable by default.
- Cache repair is explicit, not automatic.
- `fetch` is the main remote-update command.
- `fetch --depth N` fetches metadata depth, not body depth.
- Old revision bodies are fetched only when explicitly requested or needed by `show`, `diff`, or `checkout`.
