# ledecopy.py Specification

`ledecopy.py` copies only the lede from an English Wikipedia article and
creates an `mwsync.py`-compatible local draft that is ready to push to
Electowiki. It does not require authentication on enwiki or Electowiki.
enwiki and Electowiki are hardcoded.

## Command

CLI:

```bash
ledecopy.py "New York"
ledecopy.py --merge "Ohio"
```

The argument is an enwiki page title. The enwiki title determines the
Electowiki title, article key, and local filename:

```text
title: New York
key: New_York
local: New_York.mw
```

By default, `ledecopy.py` creates a new local draft for an article that does
not already exist locally or on Electowiki. Before doing any work in default
mode, the command must fail if any of the following is true:

- The local `.mw` file already exists.
- The article key is already registered in `mwsync.yaml`.
- The fetched enwiki source is a redirect (instruct the user to use the
  redirect target title instead).

Use `--merge`/`-m` only for the existing-article workflow described below.
It must not make the default new-article path less cautious.

## Merge Mode for Existing Local Checkouts

`--merge`/`-m` splices a fresh enwiki lede into an existing local checkout of
an Electowiki article. The resulting working tree should look exactly like
the user manually edited the local `.mw` file: existing Electowiki body
content above the trailing category block is preserved verbatim, the new
enwiki lede is inserted between that body and the trailing category block,
and the categories are merged. The run is followed by the normal
`mwsync.py diff` / `mwsync.py push` flow.

Merge mode is valid only when:

- The article key already exists in `mwsync.yaml`.
- The configured local `.mw` file exists.
- The configured local filename matches the key derived from the enwiki title,
  unless future title-override support explicitly permits otherwise.
- The article has been fetched from Electowiki at least once. Both
  `_cache/<Article_Key>/refs/base` and `_cache/<Article_Key>/refs/upstream`
  must exist. A bare `mwsync.py add` registration is not enough.
- The local file is a clean checkout: byte-for-byte equal to the cached body
  for `refs/base`. `--merge` must refuse to run against a dirty local copy,
  because the resulting splice would silently entangle the user's existing
  local edits with the new lede.
- The fetched enwiki source is not a redirect (same constraint as default
  mode).

If any of those checks fail, `--merge` must fail with a message identifying
the missing precondition. For the dirty-checkout case the message should
suggest `mwsync.py diff <key>` to inspect the local edits and tell the user
to commit, discard, or otherwise resolve them before retrying.

In merge mode:

- Fetch the enwiki source and extract the lede normally.
- Extract categories from the enwiki source.
- Run the normal category cache and `catmap.yaml` resolution flow, including
  interactive category Q&A when needed. Redirect substitution applies, same
  as default mode.
- Split the local file into a body section and a trailing tail block (see
  *Tail Block Detection* below).
- Build the inserted chunk: `{{Wikipedia|Title}}`, the cleaned enwiki lede,
  a `== References ==` section if the lede contains `<ref>` tags, and a
  `{{Fromwikipedia|Title|oldid=ENWIKI_REVID}}` attribution. Categories are
  not part of the inserted chunk; they are merged into the tail block
  separately.
- Merge categories: take the union of the existing local categories
  (extracted from the tail block) and the resolved enwiki categories from
  the catmap flow, de-duplicated by normalized name. Preserve the original
  order of the existing local categories; append any net-new resolved
  categories after them.
- Prompt before writing.
- If stdin is not interactive, fail rather than writing.

The resulting file order is:

```text
<existing body section, unchanged>
{{Wikipedia|Title}}
<cleaned enwiki lede>
== References ==          (only if lede has <ref> tags)
<references/>
{{Fromwikipedia|Title|oldid=ENWIKI_REVID}}
<merged categories>
<any non-category tail items (interwiki links, trailing maintenance
 templates), preserved in their original order>
```

Example confirmation text:

```text
About to merge the lede from enwiki revision 123456789 into Ohio.mw.
  Body kept: 84 lines above the original category block.
  Inserted: {{Wikipedia}} + lede + references + {{Fromwikipedia}} (22 lines).
  Categories: 5 existing + 2 new from enwiki, 7 in merged set.
Continue? [y/N]
```

Only `y` or `yes` should proceed.

### Tail Block Detection

The tail block is the trailing region of the local file that contains
categories and any closely-associated trailing content. Identify it by
walking backward from end-of-file. A line counts as a tail-block line if it
is one of:

- A blank line or whitespace-only line.
- A line starting at column zero with `[[Category:` (case-insensitive).
- A line starting at column zero with an interlanguage link pattern —
  heuristically `[[` followed by 2-3 lowercase ASCII letters and `:`.
- A single-line trailing template of the form `{{...}}` on one line.

The walk stops at the first line that does not match. Everything from that
line onward to end-of-file is the tail block; everything before it is the
body section. If the local file has no tail-block lines at all, the body is
the entire file and the inserted chunk goes at end-of-file.

## Source Fetch

Use the MediaWiki Action API. Fetch the enwiki page's current wikitext and
exact revision metadata, including at least:

```text
title
revid
timestamp
user
comment
```

The copied enwiki `revid` must be included in the generated attribution.

## Electowiki Target Check

Before writing the local draft, query Electowiki for the target article. If
the article exists, fail and instruct the user to add/fetch it from
Electowiki with `mwsync.py` first.

This target-exists failure applies only to default new-article mode. In
`--merge` mode, the existing checked-out local file (with `refs/upstream`
present) is the proof that the user is intentionally preparing an update to
an existing Electowiki article.

## Lede Extraction

The lede is the source wikitext before the first level-2 section heading:

```text
== Heading ==
```

Close-enough extraction is acceptable, but the implementation should:

- Strip `{{short description}}`.
- Strip common hatnote templates.
- Strip infobox templates.
- Strip obvious maintenance templates.
- Preserve citation templates and inline formatting that are part of readable
  lede prose.

Template stripping does not need to be perfect. Prefer conservative removal of
well-known non-prose templates over broad removal of all templates.

### Order of Operations

The lede split is sensitive to processing order. Apply steps in this order:

1. Strip top-of-page non-prose templates (short description, hatnotes,
   maintenance, infoboxes) using brace-matched removal so nested templates
   do not break extraction.
2. Find the first line-start level-2 heading (`== Heading ==` at column zero).
   Everything before that heading is the lede candidate. If no level-2
   heading exists, the entire cleaned source is the lede.
3. Extract `[[Category:...]]` links from the full original source, not just the
   lede. Categories normally live at the bottom of the article.

Splitting before stripping infoboxes is unsafe: infobox bodies and inline
tables can contain `==` patterns that look like headings.

`ledecopy.py` does not need to remove HTML comments as a separate cleanup step.
Comments that are outside the copied lede are naturally excluded by the lede
split. Comments inside the retained lede may remain for manual review.

## References

If the extracted lede contains `<ref>` tags, append:

```wikitext
== References ==
<references/>
```

Named references whose definitions live outside the lede may be missed. That
is acceptable as long as the run summary reports that references were copied
and may need review.

## Categories

In default new-article mode, extract categories found in the fetched enwiki
source using literal `[[Category:...]]` links.

Do not copy interlanguage links such as `[[fr:...]]` or `[[de:...]]`. Drop
them silently.

In `--merge` mode, category resolution still runs (catmap + cache + redirect
substitution), but the resolved enwiki categories do not replace the local
file's existing categories. Instead, the resolved set is unioned with the
existing local categories (extracted from the tail block) and de-duplicated
by normalized name, preserving the original order of the existing
categories. The confirmation prompt summarizes the merged set before the
local file is modified.

### Category Normalization

Normalize category names before lookup or prompting:

- Strip the `Category:` namespace prefix if present.
- Treat underscores and spaces equivalently.
- Trim surrounding whitespace.
- Apply MediaWiki-style first-letter capitalization.

Use the normalized form for `catmap.yaml` keys and for checking the local
Electowiki category cache. This is important because otherwise repeated imports
may miss mappings due to trivial title spelling differences.

### Category Cache

If `_cache/categories/` exists, `ledecopy.py` should use it as an optional
Electowiki category index. That cache is target-wiki state, not human mapping
state, and may eventually be maintained by `catmgr.py`.

For each normalized enwiki category not already resolved by `catmap.yaml`:

- If the cache marks the category as a redirect, do not prompt and do not
  implicit-keep. Substitute the redirect target on emit and record the
  substitution in the run summary. No `catmap.yaml` entry is written; the
  redirect itself is the routing rule and is deterministic from the cache.
- If the same category exists as a non-redirect Electowiki category page,
  keep it without prompting and do not write a `catmap.yaml` entry — the
  cache hit serves as an implicit keep.
- If the same category is used on Electowiki but has no category page, prompt
  the user with that fact surfaced. Do not silently keep undocumented
  categories.
- If the category is absent from the cache, prompt the user.
- If the cache is missing, prompt the user as in the absent case and report
  once per run that `catmgr.py fetch` would enable better suggestions.

Redirect substitution applies anywhere ledecopy is about to emit a
`[[Category:X]]` link — including catmap rename targets and user-typed
rename inputs in the interactive prompt — not only at the source-extraction
step. The rule is: if `X` is a known redirect, emit its target instead and
print a one-line note.

Hidden categories should be cached and flagged by future cache tooling. When
that flag is available, hidden/tracking categories should default toward being
dropped rather than kept.

### catmap.yaml

Durable human category decisions should live in:

```text
catmap.yaml
```

Use a flat mapping for the common cases:

```yaml
mappings:
  "California gubernatorial elections": "California"
  "Eric Swalwell": null
```

Meaning:

- String value: rename — emit `[[Category:<value>]]` instead of the source.
- `null`: drop — do not emit the category.
- Value equal to key (explicit keep): emit unchanged. Stored so the prompt
  does not recur on later imports.
- Missing key, same-name Electowiki category exists in cache: implicit keep —
  emit unchanged, no `catmap.yaml` entry written.
- Missing key, unknown to cache (or cache missing): prompt the user (TTY) or
  drop-and-report (non-interactive).

String values should point to canonical (non-redirect) Electowiki category
names. If a value points to a known redirect, ledecopy emits the redirect
target instead but does not rewrite the `catmap.yaml` entry; the user may
correct the entry by hand for cleaner state. Recorded values from the
interactive prompt are pre-substituted to the canonical target so this
mismatch does not happen for new entries.

Implicit keep (cache hit) does not need a `catmap.yaml` entry — the cache
already answers "this name is fine on Electowiki". Write an explicit-keep
entry only when the user chose `keep and save` for a category the cache could
not confirm. Without the recorded decision, the prompt would recur on every
subsequent import.

If `catmap.yaml` does not yet exist, treat it as an empty mappings file and
create it on the first recorded decision.

### Interactive Category Decisions

When `ledecopy.py` encounters a category that is not resolved by `catmap.yaml`
or by an implicit same-name cache match, it should use a crude interactive prompt
when running on a terminal:

```text
Category not known on Electowiki: California gubernatorial elections
[m] map and save  [d] drop and save  [K] keep and save  [k] keep once  [s] skip once
```

Interactive actions:

- `map and save`: ask for the Electowiki category name and write a string
  mapping to `catmap.yaml`. Tab completion should draw from the cache and
  from existing rename targets in `catmap.yaml`. If the typed name is a
  known redirect, substitute the redirect target before saving and print a
  one-line note.
- `drop and save`: write `null` to `catmap.yaml`.
- `keep and save`: write an explicit-keep entry (key equal to value) to
  `catmap.yaml` so this category is not prompted on later imports. Use this
  when the cache cannot confirm the category but the user knows the name is
  correct.
- `keep once`: emit the category for this draft but do not write a mapping.
- `skip once`: omit the category for this draft but do not write a mapping.

Recorded decisions (`map`, `drop`, `keep` with save) should be written to
`catmap.yaml` immediately as they are made, not batched until end-of-run, so
that an interrupted prompt session preserves the decisions already entered.

Detect interactivity with `sys.stdin.isatty()`. If stdin is not interactive,
do not prompt; use drop-and-report for unknown categories. The run summary
should list every category and the action that was taken (kept, mapped,
dropped, skipped, or review-needed).

## Attribution

Add an Electowiki `{{Wikipedia}}` template at the top of the generated article:

```wikitext
{{Wikipedia|New York}}
```

Add an Electowiki `{{Fromwikipedia}}` template at the bottom, before categories:

```wikitext
{{Fromwikipedia|New York|oldid=123456789}}
```

Keep the parameter construction for both templates isolated so it is easy to
adjust if Electowiki template parameter names differ from what is shown here.

## Output Shape

Generated local wikitext should use this order:

```wikitext
{{Wikipedia|Title}}

<cleaned lede text>

== References ==
<references/>

{{Fromwikipedia|Title|oldid=ENWIKI_REVID}}

[[Category:...]]
[[Category:...]]
```

Omit the references section if there are no `<ref>` tags in the copied lede.

## mwsync Integration

`ledecopy.py` should write the article entry into `mwsync.yaml` so the
article is ready for `mwsync.py push --new`. If `mwsync.yaml` does not yet
exist in the working directory, create one with the same minimal shape that
`mwsync.py init` produces, then add the article entry. If the file exists,
preserve its other articles and insert the new entry alongside them. The
pre-flight check ensures the article key is not already present.

For a new article, write at least:

```yaml
wiki:
  api_base: https://electowiki.org/w/api.php
  articles:
    New_York:
      title: New York
      url: https://electowiki.org/wiki/New_York
      local: New_York.mw
```

Do not set `upstream_revid` for a new Electowiki article candidate. The page
has not been fetched from Electowiki yet.

In `--merge` mode, do not add a new article entry. Reuse the existing
`mwsync.yaml` entry, modify only the local `.mw` file after category
resolution and confirmation, and leave every file under
`_cache/<Article_Key>/` untouched — including all of `refs/base`,
`refs/upstream`, `refs/last-pushed`, history, and revision bodies. Those
describe the Electowiki state the local checkout is based on, not the
enwiki source revision used for the splice. `catmap.yaml` may be created or
updated during category resolution, just as in default mode.

After success, print the next command:

```bash
mwsync.py push --new New_York -m "Import lede from [[wikipedia:New York]] (oldid=123456789)"
```

In `--merge` mode, suggest a normal push rather than `push --new`:

```bash
mwsync.py push Ohio -m "Merge lede from [[wikipedia:Ohio]] (oldid=123456789)"
```

The intent of `--merge` is that the resulting working tree looks exactly
like the user manually edited the local `.mw` file against the cached
Electowiki upstream. `refs/base` and `refs/upstream` are left untouched so
that `mwsync.py diff <key>` cleanly shows the spliced lede and merged
categories as a local change, and `mwsync.py push <key>` is the natural
next step. Do not re-fetch from Electowiki and do not adjust `refs/base`
to reflect the enwiki source — that would defeat the diff-then-push
workflow this mode is designed for.

## Implementation Notes

### Code Reuse from mwsync.py

`ledecopy.py` should reuse helpers from `mwsync.py` rather than reimplement
them. Shared helpers should include `load_config` and `save_config` for
`mwsync.yaml` round-tripping, `_parse_article_name` for deriving the article
key/title/local filename consistently with `mwsync.py add`, `_atomic_write` for
both the local `.mw` draft and `mwsync.yaml`, and `minimal_config` for
bootstrapping a missing `mwsync.yaml`. Importing from `mwsync.py` is acceptable;
the two scripts are sister tools that share state.

### HTTP

Set a User-Agent header on every API request, matching the convention in
`mwsync.py` (`USER_AGENT`). Wikimedia expects identifiable user agents and
may rate-limit or block anonymous unidentified clients.

### catmap.yaml I/O

Use the same atomic-write pattern that `mwsync.py` uses for `mwsync.yaml`
(temp file in the target directory, then `os.replace()`). Write `catmap.yaml`
after each recorded category decision rather than once at the end of the run,
so that decisions persist if the user interrupts a long prompt session.

Reuse `mwsync.py` config helpers where the shape allows, or write a small
mirror that takes `mappings: {…}` round-tripping through PyYAML with
`sort_keys=False` (or sorted explicitly by normalized key) and
`default_flow_style=False`, matching the readability of `mwsync.yaml`.

## Success Criteria

A successful default-mode run should:

- Write the local `.mw` draft.
- Create or extend `mwsync.yaml`.
- Create or extend `catmap.yaml` if any recorded category decisions were made.
- Report the enwiki title and copied revid.
- Report category outcomes (kept / mapped / dropped / skipped / review-needed)
  and the count of new `catmap.yaml` entries written.
- Report whether references were copied.
- Print the recommended `mwsync.py push --new` command.

A successful `--merge` run should:

- Refuse to run unless the article is already checked out locally with a
  clean working copy (local `.mw` byte-equal to the cached `refs/base`
  body).
- Run normal category mapping and create or extend `catmap.yaml` if any
  recorded category decisions were made.
- Show a splice preview (body kept / inserted chunk / merged category
  counts) before writing.
- Require interactive confirmation.
- Modify only the local `.mw` file.
- Leave `mwsync.yaml` and `_cache/` unchanged.
- Print the recommended non-`--new` `mwsync.py push` command.

Smoke tests should cover:

- A normal page with a simple lede.
- A page with `<ref>` tags.
- A page with categories.
- A page with categories where some are pre-mapped in `catmap.yaml` and some
  are not (interactive run prompts only for the unmapped ones; pre-mapped
  decisions apply silently).
- A first run in a directory where `catmap.yaml` does not yet exist (created
  on the first recorded decision).
- A non-TTY run with unknown categories (drops them, lists in summary, does
  not prompt).
- A page with a source category that is a redirect on Electowiki (emitted
  as the redirect target with a one-line note; no `catmap.yaml` entry
  written for the redirect itself).
- An existing local file (must fail).
- An article key already registered in `mwsync.yaml` (must fail).
- An existing Electowiki target page (must fail).
- `--merge` with a clean local checkout (must run category mapping, prompt
  with splice preview, and write after `yes`).
- `--merge` without a local checkout (must fail).
- `--merge` against a dirty local working copy (must fail before doing
  network work, suggesting `mwsync.py diff` to inspect the local edits).
- `--merge` in a non-interactive run (must fail before writing).
- `--merge` where the local file has no tail block at all (inserted chunk
  goes at end-of-file; merged categories collapse to the resolved enwiki
  set).

## Future Directions

Future work may add:

- Redirect resolution for enwiki titles that are redirects.
- --fromwiki and --towiki CLI options, or equivalent config-driven wiki
  selection.
- URL input in addition to page-title input.
- Target title/key/local filename overrides for cases where the Electowiki page
  name should differ from the enwiki page name.
- A dedicated `catmgr.py` or `catmap.py` command for auditing and editing
  `catmap.yaml`.
- Batch review of categories referenced by local `.mw` drafts but not yet
  represented in `catmap.yaml`.
- Better filtering for hidden, maintenance, tracking, stub, and administrative
  categories.
- Better reference handling, including named references whose definitions are
  outside the lede.
- More sophisticated template handling, possibly using a real wikitext parser
  instead of a small top-of-page template stripper.
- Optional cleanup of comments and other invisible wikitext when it is clearly
  safe.
