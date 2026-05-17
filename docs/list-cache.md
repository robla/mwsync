# Wiki Title List Cache Design

This document sketches a future wiki-level title-list cache for listing titles
on the single target wiki represented by the current `mwsync.yaml`.

Each mwsync working directory is dedicated to one wiki. The title-list cache
therefore does not need to record multiple API bases or support mixed-wiki
state. It should be refreshable local data, similar to `_cache/_categories/`,
and safe to delete and rebuild from the MediaWiki Action API.

## Goals

- Let an mwsync toolsuite command list all titles in a namespace.
- Keep the cache readable and reviewable in normal text tools.
- Support Electowiki-sized wikis easily without optimizing for huge farms.
- Avoid conflating this wiki-level index with per-article revision caches.

Non-goals for the first version:

- Caching page bodies.
- Tracking full revision history.
- Replacing per-article `_cache/<Article_Key>/` revision state.
- Supporting multiple wikis in one working directory.

## Proposed Layout

Use a reserved underscore directory named `_cache/_titles/`. The name is
intentional: this cache is an index of wiki titles and title metadata, not a
cache of page bodies or checked-out page revisions.

```text
_cache/_titles/
_cache/_titles/manifest.json
_cache/_titles/titles_ns_00.jsonl
_cache/_titles/titles_ns_10.jsonl
_cache/_titles/titles_ns_14.jsonl
```

`manifest.json` records fetch metadata:

```json
{
  "api_base": "https://electowiki.org/w/api.php",
  "fetched_at": "2026-05-17T00:00:00Z",
  "namespaces": {
    "0": {"file": "titles_ns_00.jsonl", "titles_count": 842},
    "10": {"file": "titles_ns_10.jsonl", "titles_count": 71},
    "14": {"file": "titles_ns_14.jsonl", "titles_count": 328}
  },
  "titles_count": 1241
}
```

Each `titles_ns_XX.jsonl` file stores one title per line for one namespace,
where `XX` is the namespace ID zero-padded to two digits:

```json
{"namespace":0,"title":"Maine","pageid":123,"redirect":false}
```

For namespace 10:

```json
{"namespace":10,"title":"Template:Election methods","pageid":456,"redirect":false}
```

For namespace 14:

```json
{"namespace":14,"title":"Category:Ranked voting methods","pageid":789,"redirect":true}
```

The first implementation should use namespace-sharded files, not one
`allarticles.jsonl` file. The main namespace (`0`) should be fetched by
default. Redirect titles should be included by default and shown with the
`redirect` flag. The legacy candidate name `allarticles.jsonl` should not be
used.

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
default fetch should include namespace `0` only, with options to fetch templates
(`10`), categories (`14`), project pages (`4`), or all content-like namespaces
later.

Rows should include at least:

- `namespace`
- `title`
- `pageid`
- `redirect`

Future rows may include touched timestamps, length, protection state, or
latest revision IDs if those prove useful for status and auditing.

## Initial wikimgr.py Commands

The new tool should be `wikimgr.py`, because this cache is wiki-level rather
than article-specific. The first version should support only `fetch` and
`list`:

```bash
wikimgr.py fetch --namespace 0
wikimgr.py list --namespace 0
```

- `fetch`: refresh the title-list cache for one or more namespaces. With no
  namespace argument, fetch namespace `0`.
- `list`: print cached titles in a namespace.

Do not add a `find` command initially. `wikimgr.py list --namespace 0` can be
piped into `grep`, `rg`, `fzf`, `sort`, or other shell tools. A `status`
command is useful later, but it is not part of the first implementation.

## Relationship to Other Caches

`_cache/<Article_Key>/` remains the per-article revision cache. It stores
revision bodies, refs, and history for articles the user has checked out.

`_cache/_categories/` is the intended wiki-level category index used by `catmgr.py` and
`ledecopy.py`.

`_cache/_titles/` should be a wiki-level title index. It can help users
discover page names, validate namespace listings, and drive future batch tools,
but it should not imply that listed titles are checked out locally or that page
bodies are cached.

## Open Questions

- Should category pages eventually be read from the title-list cache's namespace
  `14`, or should `_cache/_categories/` remain the specialized category source?
- Should `wikimgr.py` own only title-list cache commands, or eventually own all
  wiki-level cache commands including category cache refreshes?
- What should `wikimgr.py status` print once it exists?
