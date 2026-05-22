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

`mwsync.yaml` should distinguish three concepts:

```yaml
wiki:
  articles:
    Talk__Software:
      title: Talk:Software
      namespace: 1
      namespace_name: Talk
      dbkey: Talk:Software
      local: Talk/Software.mw
```

- `title` is the canonical MediaWiki title used for API calls.
- `namespace` is the numeric namespace ID, such as `0` for main, `1` for Talk,
  `10` for Template, and `14` for Category.
- `namespace_name` is the localized namespace label when one exists.
- `dbkey` is the MediaWiki title form with spaces normalized to underscores.
- `local` is the editable working-copy path.

The article key under `wiki.articles` should remain a stable internal
identifier, not the only place namespace information is stored. It may continue
to use a filesystem-safe encoding, but code should not infer the MediaWiki
namespace only from the key.

## Recommended Local Filename Policy

Default local paths should be namespace-aware:

```text
Software.mw
Talk/Software.mw
Template/Election_methods.mw
Category/Ranked_voting_methods.mw
```

This is more readable than `Talk__Software.mw`, avoids colons, and preserves the
human distinction between namespace and page title. It does introduce
subdirectories, but that is a reasonable tradeoff: namespace pages are already
structurally distinct from main-namespace articles.

Main-namespace pages should continue to live at the top level by default.
Non-main namespaces should use `<Namespace>/<Page_Title>.mw`. The namespace
directory should use the canonical namespace name from the target wiki, with a
safe fallback such as `ns_01` if the namespace has no stable name.

## Alternatives

`Talk__Software.mw` is compact and works in a flat directory, but it is only
safe because `mwsync.yaml` records the real title. It is not self-explanatory
and can collide with real titles that normalize similarly.

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

This should eventually be cached under a wiki-level cache directory, likely
`_cache/_titles/` or a sibling such as `_cache/_siteinfo/`. Hard-coding common
namespace IDs is acceptable as a fallback, but checkout/add should prefer the
wiki's actual namespace map when available.

## Command Behavior

`mwsync.py add Talk:Software` and `mwsync.py checkout Talk:Software` should
resolve `Talk` as a namespace prefix for the configured wiki. The resulting
entry should store `title: Talk:Software`, `namespace: 1`, and a safe default
`local`.

Lookup should remain forgiving. Users should be able to refer to a tracked page
by article key, canonical title, DB key, or configured local path:

```bash
mwsync.py status Talk:Software
mwsync.py status Talk/Software.mw
mwsync.py status Talk__Software
```

If multiple entries match a shorthand, the command should fail with an
ambiguity error and list the matching keys.

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

Do not treat `Talk__Software.mw` as the final convention. It is an acceptable
temporary escape hatch, but the better default is `Talk/Software.mw` for
non-main namespaces, with explicit namespace metadata in `mwsync.yaml`.

The first implementation step should be conservative:

1. Add helpers that parse and normalize namespace-prefixed titles.
2. Store `namespace`, `namespace_name`, and `dbkey` for newly added pages.
3. Change default local paths for non-main namespaces to `<Namespace>/<Title>.mw`.
4. Keep resolving existing `Talk__Software.mw` entries through their configured
   `local` field so current checkouts do not break.
