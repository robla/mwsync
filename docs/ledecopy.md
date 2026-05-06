# ledecopy.py v0.01 Specification

`ledecopy.py` copies only the lede from an English Wikipedia article and creates an
`mwsync.py`-compatible local draft that is ready to push to Electowiki. It does
not require authentication on enwiki or Electowiki. The first version is
intentionally narrow: enwiki and Electowiki may be hardcoded, but the code should
be structured so later versions can add `--fromwiki`, `--towiki`, or config-based
wiki selection.

## Command

Initial CLI:

```bash
ledecopy.py "New York"
```

The argument is an enwiki page title. URL input and title-renaming can be added
later. For v0.01, the enwiki title determines the Electowiki title, article key,
and local filename:

```text
title: New York
key: New_York
local: New_York.mw
```

If the local `.mw` file already exists, the command must fail unless `--force` is
provided.

## Source Fetch

Use the MediaWiki Action API. Fetch the enwiki page's current wikitext and exact
revision metadata, including at least:

```text
title
revid
timestamp
user
comment
```

The copied enwiki `revid` must be included in the generated attribution.

## Electowiki Target Check

Before writing the local draft, check whether the target Electowiki article
already exists. If it exists, fail by default and instruct the user to add/fetch
the article from Electowiki with `mwsync.py` first.

`--force` may override this and still write the local draft, but it should print a
clear warning that the target already exists and the draft may overwrite existing
Electowiki content.

## Lede Extraction

The lede is the source wikitext before the first level-2 section heading:

```text
== Heading ==
```

For v0.01, "close enough" extraction is acceptable, but the implementation should:

- Remove redirects.
- Remove HTML comments.
- Strip `{{short description}}`.
- Strip common hatnote templates.
- Strip infobox templates.
- Strip obvious maintenance templates.
- Preserve citation templates and inline formatting that are part of readable
  lede prose.

Template stripping does not need to be perfect. Prefer conservative removal of
well-known non-prose templates over broad removal of all templates.

## References

If the extracted lede contains `<ref>` tags, append:

```wikitext
== References ==
<references/>
```

Named references whose definitions live outside the lede may be missed in v0.01.
That is acceptable if the output clearly reports that references were copied and
may need review.

## Categories

For v0.01, copy categories found in the fetched enwiki source using literal
`[[Category:...]]` links. Hidden, maintenance, tracking, and administrative
categories do not need special handling yet.

Future work may add category mappings between Electowiki and enwiki, because
Electowiki category names and categorization schemes may differ from Wikipedia.

## Attribution

Add an Electowiki `{{Wikipedia}}` template at the top of the generated article:

```wikitext
{{Wikipedia|New York}}
```

Add an Electowiki `{{Fromwikipedia}}` template at the bottom, before categories:

```wikitext
{{Fromwikipedia|New York|oldid=123456789}}
```

If the actual Electowiki template parameter names differ, update this spec before
implementation or keep the parameter construction isolated so it is easy to
change.

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

`ledecopy.py` should create or update `mwsync.yaml` so the article is ready for
`mwsync.py push --new`.

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

Do not set `upstream_revid` for a new Electowiki article candidate. The page has
not been fetched from Electowiki yet.

After success, print the next command:

```bash
mwsync.py push --new New_York -m "Import lede from [[wikipedia:New York]]"
```

## Success Criteria

A successful run should:

- Write the local `.mw` draft.
- Create or update `mwsync.yaml`.
- Report the enwiki title and copied revid.
- Report whether categories and references were included.
- Print the recommended `mwsync.py push --new` command.

Minimum smoke tests should cover:

- A normal page with a simple lede.
- A page with `<ref>` tags.
- A page with categories.
- An existing local file requiring `--force`.
- An existing Electowiki target requiring either failure or explicit `--force`.
