# mwsync.py Roadmap

This roadmap assumes a clean break from the current `_cache/server--<Article_Key>.mw` layout once the next design is ready. The preferred direction is a flat-file cache with one directory per article and revision files named by MediaWiki `revid`.

## Target Cache Layout

Each article gets one cache directory:

```text
_cache/New_York/history.jsonl
_cache/New_York/refs/upstream
_cache/New_York/refs/base
_cache/New_York/refs/last-pushed
_cache/New_York/19778.mw
_cache/New_York/19778.json
_cache/New_York/19791.mw
_cache/New_York/19791.json
```

The local editable working file remains outside `_cache`:

```text
New_York.mw
```

There is no generic `latest` file. Instead, use small ref files with precise meanings. The latest known upstream revision can be inferred from chronological `history.jsonl`, but `refs/upstream` stores the same answer explicitly for robust sync operations.

## Sync Refs

Each ref file contains a single revid plus a trailing newline.

```text
_cache/New_York/refs/upstream
_cache/New_York/refs/base
_cache/New_York/refs/last-pushed
```

Meanings:

- `refs/upstream`: latest fetched wiki revision, similar to a remote-tracking branch.
- `refs/base`: upstream revision that the local working file is based on.
- `refs/last-pushed`: most recent wiki revision created by a successful `push` from this checkout.

`fetch` updates `refs/upstream` and `history.jsonl`, but should not rewrite the local working file once the merge workflow exists. `merge` reconciles `refs/base`, `refs/upstream`, and the local `.mw` file. `push` should update `refs/last-pushed` after a successful edit and then refresh `refs/upstream` and `refs/base` after confirming the new wiki revision.

These refs are intentionally more explicit than a single `latest` file. `latest` can mean latest fetched, latest pushed, or latest local base; sync code needs those states separated.

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
mwsync.py merge New_York
mwsync.py push New_York -m "Update New York article"
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

## Fetch, Merge, Push

The primitive operations should mirror git's broad shape:

- `fetch`: contact the wiki, cache revision metadata/body as requested, and update `refs/upstream`.
- `merge`: reconcile local edits with the fetched upstream revision using `refs/base` as the common ancestor.
- `push`: submit the local working file to the wiki using a safe base revision, then update sync refs after confirmation.

During early implementation, keep `fetch` and `merge` separate. The separate commands make network failures, cache failures, and merge conflicts easier to diagnose.

For merge decisions:

- Local side: `refs/base` -> `<local>.mw`
- Remote side: `refs/base` -> `refs/upstream`
- Successful merge: update the working file and then update `refs/base` to `refs/upstream`
- Clean fast-forward: if local file still matches `refs/base`, replace local file with `refs/upstream` and update `refs/base`
- Conflict: leave conflict markers or side files, and do not advance `refs/base`

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
2. Add helpers for `_cache/<Article_Key>/refs/upstream`, `refs/base`, and `refs/last-pushed`.
3. Add legacy `_cache/server--<Article_Key>.mw` detection that stops with a friendly migration message.
4. Change `fetch` to write the latest fetched revision body under `_cache/<Article_Key>/<revid>.mw`.
5. Append or merge the latest revision metadata into chronological `history.jsonl`.
6. Update `refs/upstream` on fetch, and initialize `refs/base` when creating or adopting a local working file.
7. Change `diff` to compare `New_York@upstream` against `New_York.mw`.
8. Add `log` and `show` once the manifest is stable.

Avoid compatibility code for `_cache/server--<Article_Key>.mw`. The new mainline should detect the legacy format and explain how to migrate or reset the cache, but it should not keep reading legacy snapshots as normal state.

## Legacy Boundary

Legacy handling should have two layers:

- Mainline detection: if `_cache/server--<Article_Key>.mw` exists and the new `_cache/<Article_Key>/history.jsonl` does not, stop with a friendly error.
- Optional migration code: if migration is implemented, keep it in a small script or isolated module such as `migrate_legacy_cache.py`.

The friendly error should name the detected file and offer explicit choices, for example:

```text
Legacy cache detected: _cache/server--New_York.mw
This version expects _cache/New_York/history.jsonl and revid-named files.
Run the legacy migration tool or remove _cache/server--New_York.mw and fetch again.
```

Migration code should be easy to delete once the revid cache format is the only current format. Normal `fetch`, `diff`, `merge`, and `push` code should not branch deeply on legacy paths.

## Core Invariants

- `history.jsonl` is chronological.
- `refs/upstream` should match the last valid manifest entry after a successful fetch.
- `refs/base` records the upstream revision that the local working file is based on.
- `refs/last-pushed` records successful local push activity and may be absent.
- Revid-named body files are stable by default.
- Cache repair is explicit, not automatic.
- `fetch` is the main remote-update command.
- `fetch --depth N` fetches metadata depth, not body depth.
- Old revision bodies are fetched only when explicitly requested or needed by `show`, `diff`, or `checkout`.
