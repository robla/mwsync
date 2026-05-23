# Namespace Handling

MediaWiki page titles are not plain filenames. A title such as
`Talk:Software` has a namespace prefix, while `Software` is a main-namespace
page. Treating the full title as a local filename (`Talk:Software.mw`) is
readable, but it leaks MediaWiki syntax into the filesystem. Some tools,
including `make`, interpret colons specially, so that default is not robust.

## Design Goals

- Preserve canonical MediaWiki titles exactly enough for API calls and pushes.
- Keep local filenames readable and shell-friendly.
- Avoid making filenames the source of truth for namespace semantics.
- Support future namespace-aware listing, checkout, status, and completion.
- Keep existing main-namespace filenames such as `Software.mw` simple.

## Separate Title, Key, and Local Path

`mwsync.yaml` should distinguish these concepts:

```yaml
wiki:
  articles:
    Talk__Software:
      title: Talk:Software
      namespace: 1
      namespace_name: Talk
      dbkey: Software
      local: _ns_Talk/Software.mw
```

- `title` is the canonical MediaWiki title used for API calls (e.g. `Talk:Software`).
- `namespace` is the numeric namespace ID, such as `0` for main, `1` for Talk,
  `10` for Template, and `14` for Category.
- `namespace_name` is the primary namespace prefix for this wiki, used for
  generated keys and local paths (e.g. `Talk`). Prefer the namespace map's
  local/display name because it matches canonical page titles returned by the
  wiki; fall back to the generic canonical name or `ns_<id>` only when no local
  name is available. Do not store aliases.
- `dbkey` is the MediaWiki database-key form of the page title *excluding* the
  namespace prefix, with spaces normalized to underscores (e.g. `Software`).
  This mirrors MediaWiki's `page_namespace` plus `page_title` model; the full
  human-readable title remains available as `title`.
- `local` is the editable working-copy path.

The article key under `wiki.articles` should remain a stable internal
identifier, not the only place namespace information is stored. It may continue
to use a filesystem-safe encoding, but code should not infer the MediaWiki
namespace only from the key.

For *new* entries created by `add` or `checkout`, the key is derived as:

- Main namespace: the page dbkey (e.g. `Software`).
- Other namespaces: `<NamespaceName>__<encoded-dbkey>` joined by a double underscore
  (e.g. `Talk__Software`, `Template__Election_methods`,
  `Category__Ranked_voting_methods`). `NamespaceName` is the stored
  `namespace_name`, derived from the wiki's primary namespace map entry — not
  aliases — so generated keys stay readable and deterministic for that wiki.
  The encoded DB key replaces `/` with `__`, so `User:RobLa/Journal` uses
  `User__RobLa__Journal` as its article key while preserving
  `dbkey: RobLa/Journal`.

For main-namespace articles, `namespace`, `namespace_name`, and `dbkey` may be
omitted. The resolver treats a missing `namespace` as `0` and derives the
`dbkey` from `title`. This keeps simple existing entries (e.g.
`Software: {title: Software, local: Software.mw}`) untouched. This main-
namespace derivation is normal behavior, not a legacy fallback.

The stored `url` for non-main-namespace articles should be built from the
canonical `title`, preserving the `:` between namespace prefix and page name
(e.g. `https://electowiki.org/wiki/Talk:Software`) — not from the
double-underscore article key.

## Recommended Local Filename Policy

Default local paths should be namespace-aware and shallow:

```text
Software.mw
Foo__Bar.mw
_ns_Talk/Software.mw
_ns_Template/Election_methods.mw
_ns_Category/Ranked_voting_methods.mw
_ns_User/RobLa.mw
_ns_User/RobLa__Journal.mw
```

This is more readable than putting all namespace pages in one flat directory,
avoids colons, avoids deep subpage trees, and keeps MediaWiki namespaces
visually separate from main-namespace files.

Main-namespace pages should continue to live at the top level by default.
Main-namespace subpages should not create directories; encode `/` in the DB key
as `__`, so the theoretical main-namespace page `Foo/Bar` uses `Foo__Bar.mw`.
Non-main namespaces should use `_ns_<NamespaceName>/<encoded-dbkey>.mw`, where
`<encoded-dbkey>` also replaces `/` with `__`. The namespace directory should
use the stored `namespace_name`, with spaces normalized to underscores (e.g.
`_ns_Project_talk` or a wiki-specific project-talk name for namespace 5), and a
safe fallback such as `_ns_01` if the namespace has no stable name.

## Alternatives

`Talk__Software.mw` is compact and works in a flat directory, but it is only
safe because `mwsync.yaml` records the real title. It is not self-explanatory
and can collide with real titles that normalize similarly.

`_ns/Talk/Software.mw` groups all namespace pages under one top-level
directory, but it adds an extra hierarchy level and makes shallow browsing a
little less direct.

`Talk%3ASoftware.mw` is reversible and flat, but it is less pleasant to type
and review.

`Talk--Software.mw` is readable, but still invents an escaping convention and
has collision questions.

Keeping `Talk:Software.mw` is closest to MediaWiki syntax, but it is hostile to
`make` and other tooling that gives `:` special meaning.

## Namespace Metadata Source

The target wiki's namespace table should come from the MediaWiki Action API:

```text
action=query
meta=siteinfo
siprop=namespaces|namespacealiases
format=json
formatversion=2
```

This should be cached under the wiki-level cache directory as
`_cache/_titles/namespaces.json` when namespace-aware commands need it. Core
commands should not silently fall back to hard-coded namespace IDs. If the
cache is missing or stale and the live `siteinfo` fetch fails, fail gracefully
and tell the user that the namespace map is required.

The cached JSON format should store the mapping of namespace IDs to names and
list aliases for resolution:

```json
{
  "fetched_at": "2026-05-22T22:56:00Z",
  "api_base": "https://electowiki.org/w/api.php",
  "namespaces": {
    "0": {"canonical": "", "local": ""},
    "1": {"canonical": "Talk", "local": "Talk"},
    "10": {"canonical": "Template", "local": "Template"},
    "14": {"canonical": "Category", "local": "Category"}
  },
  "aliases": {
    "talk": 1,
    "template": 10,
    "category": 14
  }
}
```

All keys in the `aliases` object must be lowercase to support case-insensitive
namespace lookup. The `canonical` and `local` strings under `namespaces` retain
their natural casing; the resolver case-folds both sides at lookup time.

The namespace map is fetched lazily the first time a command needs namespace
resolution — typically the first `add`/`checkout` of any target, or any
resolution of a colon-bearing argument that does not match an existing key or
`local` path. Once fetched it is reused for subsequent invocations. The cache
is treated as stale when its `api_base` does not match the configured
`api_base`; in that case it is re-fetched and overwritten. If the live fetch
fails (network error, unexpected response), the command should abort with a
clear error rather than guessing from a fallback table. `fetched_at` is
informational only; there is no time-based TTL in the initial implementation.

## Command Behavior

`mwsync.py add Talk:Software` and `mwsync.py checkout Talk:Software` should
resolve `Talk` as a namespace prefix for the configured wiki. The resulting
entry should store `title: Talk:Software`, `namespace: 1`, `namespace_name: Talk`,
`dbkey: Software`, and a safe default `local`.

### Lookup Resolution Algorithm

When a command receives a target string `target` representing an article, it
should resolve it using the following steps:

1. **Exact Key Match**: Check if `target` matches a key in `wiki.articles` (e.g. `Talk__Software`) directly.
2. **Local Path Match**: Check if `target` (with or without the `.mw` suffix) matches the `local` path of any registered article.
3. **Parse and Match Title**:
   - Treat `:` as the MediaWiki namespace separator. Treat `/` as a local-path
     separator only when matching configured `local` paths; do not reinterpret
     arbitrary slashes in MediaWiki titles as namespace separators.
   - If the first segment (case-insensitively, e.g. `talk` or `Talk`) is a known namespace name or alias, extract the namespace ID and treat the rest as the page title.
   - Otherwise, treat the entire string as a main-namespace (`0`) title.
   - Normalize the title proper to a DB key by trimming whitespace, collapsing
     spaces/underscores, and converting spaces to underscores.
   - Search for a registered article whose `namespace` and `dbkey` match the
     parsed namespace ID and DB key. Entries without explicit `namespace` or
     `dbkey` are first normalized as main-namespace entries by deriving
     `namespace: 0` and a DB key from `title`. Older non-main entries without
     namespace metadata should not be resolved quietly; fail with a message
     pointing at `mwsync.py migrate`.

If exactly one registered article matches, use it. If multiple match, exit with
an ambiguity error listing the candidate keys. If none match, treat it as a new
article target to be registered.

## Cache Layout Implications

Per-article revision cache paths should also stop depending directly on raw
MediaWiki titles. A future layout could use namespace-aware directories:

```text
_cache/_articles/ns_00/Software/
_cache/_articles/ns_01/Software/
```

The current `_cache/<Article_Key>/` layout can keep working while the key is
safe. The important rule is that cache paths are implementation details; the
canonical title and namespace metadata in `mwsync.yaml` are the durable state.

## Near-Term Recommendation

Do not treat `Talk__Software.mw` or `Talk/Software.mw` as the final convention.
The preferred default is `_ns_Talk/Software.mw` for non-main namespaces, with
explicit namespace metadata in `mwsync.yaml`.

The first implementation step should be conservative:

1. Add helpers that parse and normalize namespace-prefixed titles.
2. Store `namespace`, `namespace_name`, and `dbkey` for newly added non-main
   pages. Main-namespace adds may continue to write only `title`, `url`, and
   `local`.
3. Change default local paths for non-main namespaces to
   `_ns_<Namespace>/<encoded-dbkey>.mw`, creating intermediate directories on
   first write (`merge`, `checkout`).
4. During the migration window, core commands should detect existing legacy
   non-main entries and fail gracefully with a pointer to `mwsync.py migrate`.
   Do not add broad compatibility fallbacks to normal command paths.

### Migration via mwsync.py migrate

`mwsync` is new enough and has few enough users that the long-term plan
is *migration* of legacy entries, not permanent compatibility shims.
The work is split across two commands so the diagnostic stays safe and
the destructive one is explicit:

- [`fsck`](fsck.md) detects legacy entries — missing
  namespace metadata on non-main entries, colon-bearing keys (e.g.
  `Talk:Software`), and flat `local` paths for non-main-namespace pages —
  alongside its existing cache integrity checks. It does not fix them.
- [`migrate`](migrate.md) does the actual fixing. Safe metadata-only
  migrations (adding `namespace`, `namespace_name`, `dbkey` to non-main entries
  whose `title` resolves cleanly via the namespace map) apply without
  prompting. Risky migrations that touch files on disk — renaming
  literal-colon keys (`Talk:Software` → `Talk__Software`) with the
  matching `_cache/<key>/` rename, and moving flat working files
  (`Talk__Software.mw` → `_ns_Talk/Software.mw`) — prompt per-entry unless
  `--yes` is given. `--dry-run` previews every change without writing.

This split keeps `fsck` purely diagnostic in the spirit of `git fsck` /
`brew doctor`, while `migrate` is the one command that rewrites
`mwsync.yaml` and moves working files. When another `mwsync.py`
subcommand encounters a legacy-shape working directory, its error
message should point the user at `mwsync.py migrate` rather than
attempt a quiet in-place fix.

Compatibility detection for older non-main entries belongs in `fsck` and
`migrate`, not in the core resolver. Main-namespace derivation from `title`
remains normal behavior.
