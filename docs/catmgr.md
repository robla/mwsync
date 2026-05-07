# catmgr.py Specification

`catmgr.py` is a proposed companion tool for caching and inspecting the category
system for the MediaWiki instance managed by the current `mwsync.yaml`.

The immediate goal is not category mapping. The immediate goal is to keep a
local, reviewable cache of Electowiki category names so tools such as
`ledecopy.py` can tell whether an imported enwiki category already exists, is in
use, or needs human review.

Each mwsync working directory corresponds to one target wiki because
`wiki.api_base` is global for the directory. Category cache state should
therefore live under that directory's `_cache/`.

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
  "category_pages_count": 640
}
```

`allcategories.jsonl` stores one category-table row per line:

```json
{"name":"Voting theory","size":74,"pages":61,"files":0,"subcats":13,"hidden":false}
```

`category-pages.jsonl` stores category namespace pages:

```json
{"name":"Voting theory","title":"Category:Voting theory","pageid":1234,"missing":false}
```

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

For category pages:

```text
action=query
list=allpages
apnamespace=14
aplimit=max
format=json
```

Follow continuation until complete.

This two-list approach avoids conflating category pages with category-table
entries.

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

If the cache is missing, commands other than `fetch` should fail with:

```text
Category cache not found. Run: catmgr.py fetch
```

## Integration With ledecopy.py

`ledecopy.py` may use `_cache/categories/` if it exists.

For each enwiki category copied into a draft:

- If the category exists as an Electowiki category page, treat it as recognized.
- If the category is used on Electowiki but has no category page, warn that it is
  used but undocumented.
- If the category is absent from both lists, report it as review-needed.

`ledecopy.py` should not require the category cache to exist. If the cache is
missing, it can fall back to its current behavior and tell the user:

```text
Category cache not found; run catmgr.py fetch for better category review.
```

## Staleness

The cache should include `fetched_at`. `status` should report cache age. Later,
commands may warn when the cache is older than a configurable threshold, but
stale cache should not block basic local work.

## Relationship To Category Mapping

Category mapping is intentionally out of scope for the first `catmgr.py` design.
The category cache answers:

```text
What category names exist or are used on this target wiki?
```

A future mapping layer can answer:

```text
What should an enwiki category become on Electowiki?
```

The likely future mapping file is:

```text
catmap.yaml
```

That file should remain separate from `_cache/categories/` because mappings are
human decisions, while `_cache/categories/` is refreshable wiki state.

## Open Questions

- Should `allcategories.jsonl` include empty categories, or should there be a
  separate `--nonempty` mode using `acmin=1`?
- Should `category-pages.jsonl` include redirects in the category namespace, and
  should redirects be resolved?
- Should hidden categories be listed by default or hidden behind an option?
- Should `catmgr.py check` normalize underscores, spaces, and `Category:`
  prefixes exactly like MediaWiki title normalization?
- Should category cache files be committed, or should they be treated like other
  `_cache/` runtime state?

## Future Directions

- `catmap.yaml` for enwiki-to-Electowiki category mapping.
- Actions such as `keep`, `map`, `drop`, and `review`.
- `catmgr.py set "Enwiki category" --map "Electowiki category"`.
- Applying category mappings to local `.mw` drafts.
- Auditing mappings whose target Electowiki category does not exist.
- Interactive review mode for unmapped categories.
- Batch audit of all local `.mw` files.
- Detection of obvious Wikipedia maintenance/tracking categories.
- Electowiki category creation helpers that prepare local category pages for
  review before pushing.
- Optional use of Wikidata or interwiki links to suggest category mappings,
  while still requiring human confirmation.
