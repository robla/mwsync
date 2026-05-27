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
catmgr.py list --has-cat-page=false --min-pages=1
catmgr.py seed "Voting" --parent "Voting theory"
catmgr.py find "Voting"
catmgr.py check "Voting theory"
```

Meanings:

- `fetch`: refresh `_cache/categories/` from the target wiki.
- `status`: print when the cache was fetched and how many categories it has.
- `list`: print cached category names, optionally filtered by category-page
  existence and member counts.
- `seed NAME`: create a local starter category page for review. It must not
  push to the wiki.
- `find TEXT`: case-insensitive search of cached category names.
- `check NAME`: report whether `NAME` appears as a category page, a used
  category, both, or neither.

`list` supports filters that describe wiki-visible category state rather than
MediaWiki storage internals:

- `--has-cat-page=true|false|any`: filter by whether an actual `Category:`
  page exists. `any` disables the filter.
- `--has-pages=N`: filter to categories with exactly `N` normal page members.
- `--min-pages=N`: filter to categories with at least `N` normal page members.
- `--max-pages=N`: filter to categories with at most `N` normal page members.
- `--verbose`: include cached counts and redirect/hidden status.

With no filters, `list` prints all category names known from either used
categories or existing category pages.

The category-triage query for used categories that lack a category page is:

```bash
catmgr.py list --has-cat-page=false --min-pages=1
```

This is a local cache report only. It does not verify whether a matching
category page exists on Wikipedia or Wikidata.

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

Verbose `list` output includes cached member counts:

```text
Voting theory	pages=61	subcats=13	files=0	hidden=no
```

If the cache is missing, commands other than `fetch` should fail with:

```text
Category cache not found. Run: catmgr.py fetch
```

## Category Page Seeding

`seed` prepares a local, reviewable `Category:<Name>` page for a category that
is used on Electowiki but does not yet have an Electowiki category page. The
verb is intentionally not `copy` or `mirror`: generated output is starter
content that should be reviewed before `mwsync.py commit` and `mwsync.py push`.

The first useful target set is the same triage query:

```bash
catmgr.py list --has-cat-page=false --min-pages=1
catmgr.py seed "Voting" --parent "Voting theory"
catmgr.py seed "Voting" --from=enwiki
```

`seed` should register the category page with `mwsync.py`-compatible local
state or create a local `.mw` file that can be added by `mwsync.py`. The exact
mechanics should preserve the normal mwsync publishing path: `catmgr.py` may
prepare text, but `mwsync.py` remains responsible for tracking, committing, and
pushing wiki pages.

The initial implementation accepts parent categories explicitly:

```bash
catmgr.py seed "Voting" --parent "Voting theory" --parent "Electoral systems"
```

### Source Selection

`seed` should support separate source choices for parent categories and prose:

```bash
catmgr.py seed "Voting" --parents-from=manual --prose-from=none
catmgr.py seed "Voting" --parents-from=enwiki --prose-from=none
catmgr.py seed "Voting" --parents-from=enwiki --prose-from=wikidata
catmgr.py seed "Voting" --parents-from=enwiki --prose-from=enwiki
```

`--from=SOURCE` is a preset that sets both source axes:

```bash
catmgr.py seed "Voting" --from=manual
catmgr.py seed "Voting" --from=enwiki
catmgr.py seed "Voting" --from=wikidata
```

The presets expand as follows:

- `--from=manual`: `--parents-from=manual --prose-from=none`
- `--from=enwiki`: `--parents-from=enwiki --prose-from=enwiki`
- `--from=wikidata`: `--parents-from=wikidata --prose-from=wikidata`

Explicit source flags override the preset. For example:

```bash
catmgr.py seed "Voting" --from=enwiki --prose-from=none
```

means "take parent categories from enwiki, but do not copy enwiki prose."

Allowed source values:

- `--parents-from=manual`: only use explicit `--parent` values.
- `--parents-from=enwiki`: fetch the enwiki `Category:<Name>` page and extract
  its parent `[[Category:...]]` links.
- `--parents-from=wikidata`: use Wikidata/category graph data when available.
- `--prose-from=none`: generate no descriptive prose.
- `--prose-from=wikidata`: generate short original prose from Wikidata/CC0
  structured data.
- `--prose-from=enwiki`: copy or adapt prose from the enwiki category page.

Any source mode other than `manual`/`none` is explicit network behavior. It
must not be triggered by `list`, `check`, or default local-only `seed` runs.

Prefer Wikidata and other CC0 structured data for generated descriptive content
when possible. This avoids importing Wikipedia category-page prose and the
associated CC BY-SA attribution requirements.

When `--prose-from=enwiki` is used, the generated page must include
`{{Fromwikipedia|Category:<Name>|oldid=<revid>}}` or an equivalent attribution
template with the exact enwiki revision id. `--parents-from=enwiki` alone does
not require `{{Fromwikipedia}}`, because category links are being used as
organizational facts rather than copied prose.

Seeded category pages should also carry appropriate parent categories when they
are known. The initial implementation accepts explicit `--parent` values and
resolves them using the same non-interactive rules as `ledecopy.py`:

- Apply durable `catmap.yaml` decisions when present.
- Keep a parent category when the Electowiki cache shows a non-redirect
  category page.
- Substitute the redirect target when the Electowiki cache shows a category
  redirect.
- Refuse unresolved parent categories unless
  `--allow-unresolved-parents` is passed.

Interactive parent-category prompts may be added later, but should not be
required for the first useful version.

`seed` must not recursively create missing parent categories by default. If a
seeded category belongs to a parent category that is also missing, report that
parent as unresolved or as a separate candidate. Recursive category creation
should require an explicit future option because otherwise seeding one category
can unexpectedly expand into a tree of new pages.

Example starter output:

```wikitext
<!-- Starter category page generated by catmgr.py seed. Review before pushing. -->

[[Category:Voting theory]]
```

Example enwiki-prose output:

```wikitext
<copied or adapted enwiki category prose>

{{Fromwikipedia|Category:Voting|oldid=123456789}}

[[Category:Voting theory]]
```

## Shared Category Resolution

`catmgr.py` owns the shared category-resolution logic used by both category
maintenance and article imports. During an import or seed operation, the caller
passes source category links to `catmgr.py`, which consults two files in the
working directory:

1. `catmap.yaml` (defined below) — durable mapping decisions.
2. `_cache/categories/` — refreshable Electowiki category state.

For each source category encountered:

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

`ledecopy.py` should remain responsible for fetching/extracting article ledes
and calling this shared category-resolution layer. It should not carry its own
duplicate catmap, category-cache, or redirect-resolution implementation.

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

- `catmgr.py` reads and writes `catmap.yaml` through shared resolution helpers.
- `ledecopy.py` delegates import-time category decisions to `catmgr.py`.
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
- Optional use of Wikidata or interwiki links to suggest category mappings,
  while still requiring human confirmation.
