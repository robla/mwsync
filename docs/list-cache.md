# Article List Cache Design

This document sketches a future `_cache/_articles/` cache for listing pages on
the single target wiki represented by the current `mwsync.yaml`.

Each mwsync working directory is dedicated to one wiki. The article-list cache
therefore does not need to record multiple API bases or support mixed-wiki
state. It should be refreshable local data, similar to `_cache/_categories/`,
and safe to delete and rebuild from the MediaWiki Action API.

## Goals

- Let an mwsync toolsuite command list all pages in a namespace.
- Keep the cache readable and reviewable in normal text tools.
- Support Electowiki-sized wikis easily without optimizing for huge farms.
- Avoid conflating this wiki-level index with per-article revision caches.

Non-goals for the first version:

- Caching page bodies.
- Tracking full revision history.
- Replacing per-article `_cache/<Article_Key>/` revision state.
- Supporting multiple wikis in one working directory.

## Proposed Layout

Use a reserved underscore directory:

```text
_cache/_articles/
_cache/_articles/manifest.json
_cache/_articles/allarticles.jsonl
```

`manifest.json` records fetch metadata:

```json
{
  "api_base": "https://electowiki.org/w/api.php",
  "fetched_at": "2026-05-17T00:00:00Z",
  "namespaces": [0, 10, 14],
  "pages_count": 1234
}
```

`allarticles.jsonl` stores one page per line:

```json
{"namespace":0,"title":"Maine","pageid":123,"redirect":false}
{"namespace":10,"title":"Template:Election methods","pageid":456,"redirect":false}
{"namespace":14,"title":"Category:Ranked voting methods","pageid":789,"redirect":true}
```

This single-file layout is the simplest starting point. It is easy to grep,
sort, diff, and regenerate.

## Alternative Layouts

A namespace-sharded layout may become nicer if `allarticles.jsonl` grows:

```text
_cache/_articles/namespaces/0.jsonl
_cache/_articles/namespaces/10.jsonl
_cache/_articles/namespaces/14.jsonl
```

Advantages:

- Listing one namespace reads only one file.
- Diffs are smaller when a namespace changes.
- Namespace-specific metadata can evolve independently.

Disadvantages:

- More files and slightly more bookkeeping.
- Cross-namespace search must read multiple files.

Another option is one directory per namespace with a manifest per namespace,
but that is probably premature for Electowiki-scale use.

## Fetch Source

Use the MediaWiki Action API configured by `wiki.api_base`:

```text
action=query
list=allpages
apnamespace=NAMESPACE_ID
aplimit=max
apfilterredir=all
format=json
formatversion=2
```

Follow continuation until complete. Repeat for each requested namespace. The
initial default should probably include namespace `0` only, with options to
fetch templates (`10`), categories (`14`), project pages (`4`), or all
content-like namespaces later.

Rows should include at least:

- `namespace`
- `title`
- `pageid`
- `redirect`

Future rows may include touched timestamps, length, protection state, or
latest revision IDs if those prove useful for status and auditing.

## Candidate Commands

The command name is still open. Possibilities:

```bash
artmgr.py fetch --namespace 0
artmgr.py list --namespace 0
artmgr.py find Maine
catmgr.py articles fetch --namespace 0
mwsync.py list --namespace 0
```

The important behavior is independent of the command spelling:

- `fetch`: refresh `_cache/_articles/` for one or more namespaces.
- `list`: print cached titles in a namespace.
- `find TEXT`: search cached titles case-insensitively.
- `status`: show cache age, namespaces covered, and page counts.

## Relationship to Other Caches

`_cache/<Article_Key>/` remains the per-article revision cache. It stores
revision bodies, refs, and history for articles the user has checked out.

`_cache/_categories/` is the wiki-level category index used by `catmgr.py` and
`ledecopy.py`.

`_cache/_articles/` should be a wiki-level page index. It can help users
discover page names, validate namespace listings, and drive future batch tools,
but it should not imply that listed pages are checked out locally.

## Open Questions

- Should the first implementation use one `allarticles.jsonl` file or
  namespace-sharded files?
- Which namespaces should be fetched by default?
- Should redirects be included by default, filtered out, or shown with a flag?
- Should category pages eventually be read from `_cache/_articles/` namespace
  `14`, or should `_cache/_categories/` remain the specialized category source?
- Should this live in a new tool such as `artmgr.py`, in `mwsync.py list`, or
  as a subcommand group on an existing helper?
