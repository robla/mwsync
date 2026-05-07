# ledecopy.py Specification

`ledecopy.py` copies only the lede from an English Wikipedia article and
creates an `mwsync.py`-compatible local draft that is ready to push to
Electowiki. It does not require authentication on enwiki or Electowiki.
enwiki and Electowiki are hardcoded.

## Command

CLI:

```bash
ledecopy.py "New York"
```

The argument is an enwiki page title. The enwiki title determines the
Electowiki title, article key, and local filename:

```text
title: New York
key: New_York
local: New_York.mw
```

Before doing any work, the command must fail if any of the following is
true:

- The local `.mw` file already exists.
- The article key is already registered in `mwsync.yaml`.
- The fetched enwiki source is a redirect (instruct the user to use the
  redirect target title instead).

There is no override flag. Fix the conflict before re-running.

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
Electowiki with `mwsync.py` first. There is no override flag.

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

Copy categories found in the fetched enwiki source using literal
`[[Category:...]]` links. Hidden, maintenance, tracking, and administrative
categories do not need special handling.

Do not copy interlanguage links such as `[[fr:...]]` or `[[de:...]]`. Drop
them silently.

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

After success, print the next command:

```bash
mwsync.py push --new New_York -m "Import lede from [[wikipedia:New York]]"
```

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

## Success Criteria

A successful run should:

- Write the local `.mw` draft.
- Create or extend `mwsync.yaml`.
- Report the enwiki title and copied revid.
- Report whether categories and references were included.
- Print the recommended `mwsync.py push --new` command.

Smoke tests should cover:

- A normal page with a simple lede.
- A page with `<ref>` tags.
- A page with categories.
- An existing local file (must fail).
- An article key already registered in `mwsync.yaml` (must fail).
- An existing Electowiki target page (must fail).

## Future Directions

Future work may add:

- Redirect resolution for enwiki titles that are redirects.
- --fromwiki and --towiki CLI options, or equivalent config-driven wiki
  selection.
- URL input in addition to page-title input.
- Target title/key/local filename overrides for cases where the Electowiki page
  name should differ from the enwiki page name.
- `--force` or another explicit override flow for advanced users who knowingly
  want to overwrite a local draft or prepare changes for an existing Electowiki
  page.
- Category mapping between enwiki and Electowiki categories.
- Filtering for hidden, maintenance, tracking, stub, and administrative
  categories.
- Better reference handling, including named references whose definitions are
  outside the lede.
- More sophisticated template handling, possibly using a real wikitext parser
  instead of a small top-of-page template stripper.
- Optional cleanup of comments and other invisible wikitext when it is clearly
  safe.
