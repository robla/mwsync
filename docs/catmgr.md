# catmgr.py Specification

`catmgr.py` is a proposed companion tool for caching and inspecting the category
system for the MediaWiki instance managed by the current `mwsync.yaml`.

The category subsystem in a working directory has three pieces:

- `_cache/categories/` — refreshable cache of category names and usage on the
  target wiki. Owned by `catmgr.py`.
- `catmap.yaml` — durable per-category decisions (rename, drop, explicit keep)
  for this working directory. Edited by `ledecopy.py` during import and read
  back on subsequent runs so the same prompt does not recur.
- `ledecopy.py` — the primary editor of `catmap.yaml`. When an imported
  article has an enwiki category not yet in `catmap.yaml`, `ledecopy.py`
  prompts the user with whatever context the cache can provide and saves
  the answer.

`catmgr.py`'s own scope is the cache piece. The `catmap.yaml` shape is also
defined in this document because the cache and the map are designed together
and `ledecopy.py` uses both.

Each mwsync working directory corresponds to one target wiki because
`wiki.api_base` is global for the directory. Cache and mapping state both live
under that directory.

## Practicality

Keeping a local copy of all Electowiki category names is practical.

Electowiki is small enough that the complete category index should be cheap to
fetch and store. A live API check on May 6, 2026 reported roughly 4,057 pages and
842 content articles from Electowiki site statistics. The `allcategories` API
returns up to 500 category rows per request; the first batch alone was about 37
KB and indicated more results. Even several thousand categories would fit easily
in a small text cache.

The cache should still be treated as refreshable state, not source truth. It may
be stale, and MediaWiki category tables can include empty or previously used
categories.

## Category Meanings

MediaWiki exposes more than one useful category concept:

- A **category page** is a page in namespace 14, such as
  `Category:Voting theory`.
- A **used category** is a category known to MediaWiki's category table, usually
  because pages or subcategories belong to it.
- A **redlink category** may be used by pages even when no category page exists.

For import decisions, `catmgr.py` should cache both existing category pages and
used categories. A category that is used but has no category page may still be a
reasonable target, but it should be reported differently from a category with a
real page.

## Cache Layout

Use a dedicated category cache directory:

```text
_cache/categories/
_cache/categories/manifest.json
_cache/categories/allcategories.jsonl
_cache/categories/category-pages.jsonl
```

`manifest.json` records fetch metadata:

```json
{
  "api_base": "https://electowiki.org/w/api.php",
  "fetched_at": "2026-05-06T00:00:00Z",
  "allcategories_count": 812,
  "category_pages_count": 640,
  "category_redirects_count": 47
}
```

`allcategories.jsonl` stores one category-table row per line:

```json
{"name":"Voting theory","size":74,"pages":61,"files":0,"subcats":13,"hidden":false}
```

`category-pages.jsonl` stores one row per category-namespace page, with
redirect status resolved during fetch:

```json
{"name":"Voting theory","title":"Category:Voting theory","pageid":1234,"redirect":false}
{"name":"Preferential voting methods","title":"Category:Preferential voting methods","pageid":2345,"redirect":true,"redirect_target":"Ranked voting methods"}
```

`redirect_target` is the normalized name (no `Category:` prefix) of the
target. It is omitted when `redirect` is false.

The files should be deterministic and readable:

- Sort by normalized category name.
- Write atomically.
- Use UTF-8.
- Do not require authentication.

## Fetch Sources

Use the MediaWiki Action API configured by `wiki.api_base`.

For used categories:

```text
action=query
list=allcategories
aclimit=max
acprop=size|hidden
format=json
```

Follow continuation until complete.

For category pages, enumerate namespace 14 and resolve redirects in the same
query by combining `generator=allpages` with `prop=info` and `redirects=1`:

```text
action=query
generator=allpages
gapnamespace=14
gaplimit=max
prop=info
redirects=1
format=json
formatversion=2
```

The response's `query.pages` lists canonical (non-redirect) titles after
auto-resolution; `query.redirects` lists `{from, to}` pairs for any redirect
category pages. Persist non-redirect rows with `redirect: false`. For each
entry in `query.redirects`, persist a row with `redirect: true` and
`redirect_target` set to the normalized target name.

Follow continuation until complete.

This two-list approach avoids conflating category pages with category-table
entries, and the redirect resolution lets `ledecopy.py` route emitted
categories through redirects to their canonical targets without writing a
redirected category into the local draft.

## Proposed Commands

Initial commands should focus on cache maintenance and inspection:

```bash
catmgr.py fetch
catmgr.py status
catmgr.py list
catmgr.py find "Voting"
catmgr.py check "Voting theory"
```

Meanings:

- `fetch`: refresh `_cache/categories/` from the target wiki.
- `status`: print when the cache was fetched and how many categories it has.
- `list`: print cached category names.
- `find TEXT`: case-insensitive search of cached category names.
- `check NAME`: report whether `NAME` appears as a category page, a used
  category, both, or neither.

Example `check` output:

```text
Category:Voting theory
  category page: yes
  used category: yes
  members: 74 total, 61 pages, 13 subcategories, 0 files
```

For a redirect category, `check` should also print the redirect target:

```text
Category:Preferential voting methods
  category page: yes (redirect to "Ranked voting methods")
  used category: no
```

If the cache is missing, commands other than `fetch` should fail with:

```text
Category cache not found. Run: catmgr.py fetch
```

## Integration With ledecopy.py

`ledecopy.py` is the primary consumer and editor of category state. During an
import, it walks the categories from the enwiki source and consults two files
in the working directory:

1. `catmap.yaml` (defined below) — durable mapping decisions.
2. `_cache/categories/` — refreshable Electowiki category state.

For each enwiki category encountered:

- If `catmap.yaml` has a recorded decision (rename, drop, or explicit keep),
  apply it without prompting.
- Otherwise, prompt the user. The prompt should surface, at minimum, the
  source category name, what the cache says about it (exists as a category
  page, used but no page, absent, or cache missing), and the available
  actions. Record the user's answer in `catmap.yaml` so the same prompt
  does not recur on later imports.

When a category about to be emitted is a redirect according to the cache —
whether the source name itself, a `catmap.yaml` rename target, or a name the
user typed in the rename prompt — `ledecopy.py` should substitute the
redirect target before writing the draft, and print a one-line note such as
`"Preferential voting methods" is a redirect on Electowiki to "Ranked voting
methods"; using "Ranked voting methods".`. Redirect substitution is
deterministic, so it does not require a confirmation prompt; the run summary
should list how many categories were routed via redirect.

If `_cache/categories/` is missing, prompts still work but lose the
"exists on Electowiki?" hint. Tell the user once per run:

```text
Category cache not found; run catmgr.py fetch for better suggestions.
```

If stdin is not a TTY, `ledecopy.py` must not prompt. It should fall back to
a defined batch policy (drop unknown categories and list them in the run
summary as review-needed) and exit successfully. Re-running interactively
later picks up the unmapped names and prompts for them.

## Staleness

The cache should include `fetched_at`. `status` should report cache age. Later,
commands may warn when the cache is older than a configurable threshold, but
stale cache should not block basic local work.

## Mapping File (catmap.yaml)

`catmap.yaml` lives in the working directory next to `mwsync.yaml`. It records
every per-category decision that has been made for this target wiki. The file
is intentionally simple so it can be reviewed as a diff and edited by hand.

Shape:

```yaml
mappings:
  "California gubernatorial elections": "California"
  "Voting theory": "Voting theory"
  "Eric Swalwell": null
```

Value semantics:

- Scalar string — rename: emit `[[Category:<value>]]` in place of the source
  category.
- `null` — drop: do not emit the category at all.
- Same string as the key — explicit keep: emit unchanged. Stored even though
  it looks redundant, so the user is not re-prompted for the same name on
  every import.

Keys are normalized the same way MediaWiki normalizes category titles:
underscores replaced with spaces, leading and trailing whitespace trimmed,
first letter capitalized, no `Category:` prefix. `ledecopy.py` and
`catmgr.py check` must apply the same normalization before lookup, otherwise
catmap entries can silently miss matching categories.

Scope of ownership:

- `ledecopy.py` reads and writes `catmap.yaml` during import.
- `catmgr.py` does not modify `catmap.yaml`. It may read the file in future
  audit/review commands, but editing stays with `ledecopy.py` until a
  dedicated mapping CLI is designed.
- The file is separate from `_cache/categories/` because mapping decisions
  are durable human input, while the cache is refreshable wiki state.

## Open Questions

- Should `allcategories.jsonl` include empty categories, or should there be a
  separate `--nonempty` mode using `acmin=1`?
- Should hidden categories be listed by default or hidden behind an option?
- Should `catmgr.py check` normalize underscores, spaces, and `Category:`
  prefixes exactly like MediaWiki title normalization? (`catmap.yaml` lookups
  in `ledecopy.py` need the same normalization, so settling this affects both
  tools.)
- Should category cache files be committed, or should they be treated like other
  `_cache/` runtime state?
- Should the `ledecopy.py` prompt offer a "skip / decide later" action that
  applies the category once but does not write to `catmap.yaml`, so the user
  can defer a hard call without committing to a recorded decision?
- Should `ledecopy.py` allow re-prompting for an already-decided category
  (e.g. `--re-review`), or is editing `catmap.yaml` by hand the intended way
  to revise past decisions?

## Future Directions

- A dedicated mapping CLI under `catmgr.py` (e.g. `catmgr.py map set`,
  `catmgr.py map list`, `catmgr.py map audit`) so editing `catmap.yaml` is
  not exclusive to `ledecopy.py`.
- Retroactive application of `catmap.yaml` updates to existing local `.mw`
  drafts.
- Auditing mappings whose target Electowiki category does not exist in the
  cache.
- Batch audit of all local `.mw` files for unmapped categories without
  running an import.
- Detection of obvious Wikipedia maintenance/tracking categories so they can
  be dropped without prompting.
- Electowiki category creation helpers that prepare local category pages for
  review before pushing.
- Optional use of Wikidata or interwiki links to suggest category mappings,
  while still requiring human confirmation.
