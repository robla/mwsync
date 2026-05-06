# Legacy mwsync.py Format

This document describes the current `mwsync.py` storage model before the planned revid-based cache redesign. It is intended to preserve enough detail to write old-to-new migration scripts later.

## Scope

This legacy format is the version that stores one mutable server snapshot per article:

```text
_cache/server--<Article_Key>.mw
```

It has no per-revision body files, no `history.jsonl`, and no persistent local history beyond metadata in `mwsync.yaml`.

## Required Files

The tool expects to run in a directory containing:

```text
mwsync.yaml
```

Each fetched article usually also has:

```text
<local>.mw
_cache/server--<Article_Key>.mw
```

The local `.mw` file is the editable working copy. The `_cache/server--*.mw` file is the last fetched upstream text used by `diff` and `difftool`.

## Config Shape

`mwsync.yaml` is loaded with `PyYAML`. Its expected shape is:

```yaml
wiki:
  api_base: https://electowiki.org/w/api.php
  articles:
    New_York:
      title: New York
      url: https://electowiki.org/wiki/New_York
      local: New_York.mw
      upstream_revid: 19778
      upstream_timestamp: '2026-03-18T05:04:08Z'
      upstream_editor: RobLa
      upstream_summary: Adding section
      upstream_sha1: bbdf5e976cb09fe16f9efa45e4612f5fca5f9e44
      last_pushed_revid: 19778
      last_pushed_at: '2026-03-18T05:04:08Z'
```

`wiki.api_base` is optional. If missing, the code defaults to:

```text
https://electowiki.org/w/api.php
```

Each key under `wiki.articles` is the canonical article key. `local` is optional in practice; if absent, code falls back to `<Article_Key>.mw`.

## Article Resolution

Current `resolve_article_entry()` accepts either:

- the canonical article key, such as `New_York`
- the configured local filename, such as `New_York.mw`

It returns the canonical article key plus the article config entry. This matters because cache paths are derived from the canonical key, not from the raw command argument.

If multiple articles use the same `local` filename, lookup by local filename exits with an ambiguity error.

## Snapshot Path Rule

The legacy server snapshot path is computed as:

```python
os.path.join("_cache", f"server--{key}.mw")
```

For article key `New_York`, the snapshot is:

```text
_cache/server--New_York.mw
```

This path is mutable. A new `fetch`, `push` auto-refetch, or `diff --remote` may overwrite it.

## Fetch Behavior

`fetch ARTICLE`:

1. Resolves `ARTICLE` to a canonical key and article entry.
2. Uses the article title and `wiki.api_base` to call the MediaWiki Action API.
3. Fetches current wikitext and revision metadata with `action=query&prop=revisions`.
4. Writes the wikitext to the local working file.
5. Writes the same wikitext to `_cache/server--<Article_Key>.mw`.
6. Updates `upstream_revid`, `upstream_timestamp`, `upstream_editor`, `upstream_summary`, and `upstream_sha1` in `mwsync.yaml`.

Before overwriting an existing local file, `fetch` runs:

```bash
git status --porcelain -- <local>
```

If the local file has uncommitted changes, `fetch` exits unless `--force` is used.

`fetch --dry-run` prints the intended title, API URL, local path, snapshot path, and current `upstream_revid`. It writes nothing.

## Push Behavior

`push ARTICLE`:

1. Resolves `ARTICLE`.
2. Requires `upstream_revid` unless `--new` is used.
3. Reads the local `.mw` file.
4. Gets an edit summary from `-m/--message` or from `$VISUAL`, `$EDITOR`, or `vi`.
5. Logs in with `MWSYNC_MW_USER` and `MWSYNC_MW_PASSWORD`.
6. Gets a CSRF token.
7. Submits `action=edit` using `baserevid` for existing pages or `createonly` for new pages.
8. Writes `last_pushed_revid` and `last_pushed_at` to `mwsync.yaml`.
9. Immediately re-fetches the page and rewrites both the local `.mw` file and `_cache/server--<Article_Key>.mw`.

The post-push re-fetch also updates the upstream metadata fields in `mwsync.yaml`.

## Diff Behavior

`diff ARTICLE` compares:

```text
_cache/server--<Article_Key>.mw
<local>.mw
```

using:

```bash
git diff --no-index <snapshot> <local>
```

`diff --remote ARTICLE` first refreshes the server snapshot from the wiki, but does not rewrite the local working file or update `mwsync.yaml`.

`difftool ARTICLE` launches:

```bash
meld <snapshot> <local>
```

Both commands fail if the snapshot file is missing.

## Status Behavior

`status` iterates over `wiki.articles` and prints:

- article key
- local path
- git cleanliness for the local file
- `upstream_revid`, timestamp, and editor
- `last_pushed_revid` and `last_pushed_at`

`status ARTICLE` accepts either article key or local filename through `resolve_article_entry()`.

## API Details

The legacy code uses the MediaWiki Action API at `w/api.php`, not the REST API.

Fetch requests use:

```text
action=query
format=json
prop=revisions
rvprop=content|ids|timestamp|user|comment|sha1
titles=<title>
formatversion=2
```

The returned revision fields mapped into `mwsync.yaml` are:

- `content` -> local file and snapshot body
- `revid` -> `upstream_revid`
- `timestamp` -> `upstream_timestamp`
- `user` -> `upstream_editor`
- `comment` -> `upstream_summary`
- `sha1` -> `upstream_sha1`

## Migration Notes

A migration script can build the new per-article cache from legacy state without refetching, as long as the legacy snapshot exists and `upstream_revid` is set.

Migration support should be separated from normal sync logic. Prefer a small script or isolated module such as `migrate_legacy_cache.py` rather than embedding legacy reads throughout `fetch`, `diff`, `merge`, or `push`. Once the new cache format is the only current usage, the migration code should be easy to delete.

The mainline code should still detect legacy cache files and fail clearly. If `_cache/server--<key>.mw` exists but `_cache/<key>/history.jsonl` does not, the user-facing error should explain that the old cache format was found and that the user should either run the migration tool or remove the legacy snapshot and fetch again.

For each article:

1. Read `wiki.articles.<key>` from `mwsync.yaml`.
2. Determine `local = art.get("local", key + ".mw")`.
3. Determine `snapshot = _cache/server--<key>.mw`.
4. Determine `revid = art["upstream_revid"]`.
5. Create `_cache/<key>/`.
6. Copy `snapshot` to `_cache/<key>/<revid>.mw`.
7. Write `_cache/<key>/<revid>.json` from the metadata fields in `mwsync.yaml`.
8. Write or merge one chronological `history.jsonl` line for `revid`.
9. Create `_cache/<key>/refs/`.
10. Write `revid` to `_cache/<key>/refs/upstream`.
11. Write `revid` to `_cache/<key>/refs/base` if the local file is known to match the legacy snapshot.
12. Write `last_pushed_revid` to `_cache/<key>/refs/last-pushed` if that field exists.

Suggested `history.jsonl` record:

```json
{"revid":19778,"timestamp":"2026-03-18T05:04:08Z","user":"RobLa","comment":"Adding section","sha1":"bbdf5e976cb09fe16f9efa45e4612f5fca5f9e44","body":"19778.mw","meta":"19778.json","source":"legacy-snapshot"}
```

If `snapshot` is missing but `local` exists, do not assume the local file is identical to upstream. A migration script should either skip body migration for that article or refetch the revision from the wiki.

If `upstream_revid` is missing, the article has not been fetched in the legacy format. A migration script should create no revision body and leave history empty until the next fetch.

If `upstream_sha1` exists, migration can optionally verify the copied body by computing the MediaWiki-compatible SHA-1 if an implementation is available. The current script does not perform this verification locally.

## Migration Ref Rules

The new design separates sync state into explicit ref files. Legacy state maps into those refs as follows:

```text
upstream_revid     -> refs/upstream
upstream_revid     -> refs/base, only when local matches snapshot
last_pushed_revid  -> refs/last-pushed
```

`refs/upstream` means the latest fetched wiki revision. It can be written whenever `upstream_revid` exists and the matching body is available or fetchable.

`refs/base` means the upstream revision that the local working file is based on. During migration, it should be written only when the migration can establish that `<local>.mw` and `_cache/server--<Article_Key>.mw` are identical. If the local file differs from the snapshot, leave `refs/base` absent or require an explicit migration mode, because the local file may contain unmerged edits.

`refs/last-pushed` preserves the old push bookkeeping. It may differ from `refs/upstream` if someone else edited the page after the last local push, or if the legacy state is stale.

## Fields With No New Equivalent Yet

`last_pushed_at` describes local push activity, but the proposed ref file stores only the revision ID. A migration script should preserve `last_pushed_at` in `mwsync.yaml` or copy it into the `<revid>.json` sidecar unless the new design introduces a separate push log.

The legacy snapshot filename does not store a revid. The only reliable revid source is `mwsync.yaml`.

## Cleanup After Migration

After a successful migration and after the new code no longer reads legacy snapshots, these files can be removed:

```text
_cache/server--<Article_Key>.mw
```

The editable local files should remain:

```text
<local>.mw
```
