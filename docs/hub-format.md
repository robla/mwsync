# Hub Format

Status: draft 0.1  
Intended location: `docs/hub-format.md`  
Applies to: `mwmap.py` and any future successor to `mwsync.py`  
Last updated: 2026-06-22

## 1. Purpose

`mwmap.py` is intended to synchronize document content across multiple upstreams: MediaWiki sites, local wiki notebooks, Google Docs, file trees, and other document systems. The hub format is the canonical local representation used by `mwmap.py` when content from one upstream needs to be compared, merged, edited, and pushed to another upstream.

The hub format is deliberately not a custom abstract syntax tree. The canonical representation must be a hand-editable text format that a maintainer can inspect in a normal editor, review in Git, patch manually, and still understand if `mwmap.py` disappears.

The current hub format is:

> **Portable MediaWiki wikitext, with explicit preservation rules for non-portable constructs.**

This document defines the initial profile of MediaWiki wikitext that `mwmap.py` should treat as portable, the constructs that require warnings or raw preservation, and the expectations for adapters that import from or export to upstream systems.

**Refinement (2026-06-22):** for MediaWiki specifically, the per-upstream representation is now exact, raw wikitext stored byte-for-byte, with the upstream treated as authoritative (including its post-save transforms) and no mwmap-specific markers written into the file. The "portable profile" below is therefore best read as a candidate *neutral pivot* for crossing between different upstreams, not as a rewrite of the MediaWiki mirror. The broader question — which existing, collaboratively-edited, eyeball-auditable format(s) best serve as the core format and can outlive `mwmap.py` — is taken up in §26.

## 2. Non-goals

The hub format is not intended to be a perfect representation of every upstream.

It is also not intended to become a private document language that competes with MediaWiki, Org, Markdown, Typst, LaTeX, Google Docs, or ZimWiki.

Specifically, this document does not attempt to define:

- a complete MediaWiki parser;
- a replacement for Parsoid;
- a full Google Docs document model;
- a full Org-mode parser;
- a typesetting language;
- a private JSON AST that becomes the real source of truth.

Implementation code may use DOMs, JSON objects, Pandoc ASTs, Parsoid pagebundles, or Google Docs API structures internally. Those are conversion machinery. They are not the canonical hub format unless a future version of this specification explicitly says otherwise.

## 3. Design principles

### 3.1 The canonical source must be text

The hub representation must be plain text. It must be possible to open the file in Emacs, Vim, nano, VS Code, or a terminal pager and understand the document structure.

### 3.2 MediaWiki compatibility matters

MediaWiki wikitext is the starting point because `mwmap.py` is expected to work deeply with MediaWiki sites such as Electowiki and Wikipedia. Wikitext has decades of compatibility pressure behind it and is already the native source language for the most important initial upstreams.

### 3.3 Portability is a profile, not a promise about all wikitext

Arbitrary Wikipedia-grade wikitext is not automatically portable. The hub format is a constrained profile of MediaWiki wikitext plus explicit escape hatches for constructs that cannot be represented cleanly in other systems.

### 3.4 Loss must be visible

When a conversion loses information, simplifies structure, or preserves a construct opaquely, `mwmap.py` should record that fact in a machine-readable and human-readable way.

A silent lossy conversion is a bug unless the user explicitly requested a lossy export.

### 3.5 Round-tripping is more important than pretty output

The hub file should favor stable round-tripping over typographic beauty. Typst, LaTeX, HTML, PDF, and Google Docs may be excellent output targets, but they should not drive the canonical source format.

### 3.6 Generated representations are disposable

Adapters may generate Parsoid HTML, Pandoc JSON, Google Docs API request batches, intermediate HTML, or other structured forms. These generated artifacts may be cached for performance and debugging, but the `.mw` hub file remains the canonical editable source.

## 4. Conceptual architecture

A typical conversion path should look like this:

```text
upstream A
   |
   v
adapter A
   |
   v
portable wikitext hub
   |
   v
adapter B
   |
   v
upstream B
```

For example:

```text
Google Doc <=> Google Docs adapter <=> hub wikitext <=> MediaWiki adapter <=> Wikipedia page
```

or:

```text
ZimWiki page <=> Zim adapter <=> hub wikitext <=> MediaWiki adapter <=> Electowiki page
```

or:

```text
Org file subtree <=> Org adapter <=> hub wikitext <=> Google Docs adapter <=> Google Doc
```

The hub format is the point where `mwmap.py` performs local diffing, conflict detection, hand edits, and cross-upstream mapping.

## 5. File identity and storage

A hub document represents one logical document or page, not necessarily one upstream page. A single hub document may map to:

- one MediaWiki page;
- one Google Doc;
- one ZimWiki page;
- one Org subtree;
- one Markdown file;
- one future upstream object.

The initial implementation should prefer one hub file per logical page.

Recommended file extension:

```text
.mw
```

Recommended repository layout:

```text
mwmap-repo/
  docs/
  pages/
    Electowiki/
      Approval_voting.mw
      STAR_voting.mw
    Wikipedia/
      Example.mw
  maps/
    upstreams.toml
    page-maps.toml
  cache/
    generated/
    upstream/
  reports/
```

This layout is advisory. The hub format itself does not require a specific repository layout.

## 6. Metadata

### 6.1 Keep page content clean

Hub documents should avoid heavy front matter. In particular, the hub format should not require YAML front matter.

Most mapping metadata belongs in sidecar files managed by `mwmap.py`, such as TOML, SQLite, JSON, or another explicit mapping store.

The page body should remain mostly valid MediaWiki wikitext.

### 6.2 No mwmap markers in mirror files

An earlier draft suggested an optional metadata comment at the top of a hub
file. That is now rejected for per-upstream **mirror** files: a MediaWiki `.mw`
mirror is an exact, byte-for-byte copy of the upstream revision, so `mwmap.py`
must not inject `<!-- mwmap-... -->` lines into it. Such lines would break
byte-exactness against the upstream, can be rewritten or relocated by the
wiki's own post-save transforms, and any unknown construct that survives to
render is a leak. All synchronization metadata therefore lives in sidecars
(§6.3), never in the mirror body.

A self-describing comment header may still be acceptable inside a *pivot* file
that is guaranteed never to be pushed raw, but `mwmap.py` should not require
one.

### 6.3 Sidecar metadata

The following information should usually live outside the hub document:

- upstream IDs;
- source URLs;
- revision IDs;
- ETags;
- timestamps;
- OAuth account bindings;
- MediaWiki site API endpoints;
- Google Docs document IDs;
- Zim notebook root paths;
- Org file paths and subtree IDs;
- local conflict state;
- last successful push or pull state.

Sidecar metadata should describe synchronization state. The hub file should describe the document.

## 7. Portable wikitext profile

This section describes a candidate **neutral pivot** (see §26), not the
MediaWiki mirror. For same-site MediaWiki round-trips the mirror is exact raw
wikitext and none of the profiling or normalization below applies; the profile
matters only when bridging to a different upstream.

The portable profile is the subset of wikitext that adapters should attempt to preserve across all reasonable targets.

### 7.1 Headings

Use standard MediaWiki heading syntax:

```mediawiki
= Top-level title =
== Section ==
=== Subsection ===
```

Adapters should preserve heading order and level. When exporting to systems that have a document title separate from body headings, the adapter may map the first level-1 heading to the upstream title, but it must avoid duplicating titles unless configured to do so.

### 7.2 Paragraphs

Blank-line-separated paragraphs are portable.

```mediawiki
This is one paragraph.

This is another paragraph.
```

Hard line breaks inside paragraphs should be treated cautiously. They are often editor artifacts and may not round-trip reliably through Google Docs or visual editors.

### 7.3 Emphasis

The portable profile supports MediaWiki bold and italic markup:

```mediawiki
''italic''
'''bold'''
'''''bold italic'''''
```

Adapters should preserve bold and italic when the target supports them.

### 7.4 Internal links

Internal wiki links are portable as semantic links, even when the target does not have an identical concept.

```mediawiki
[[Approval voting]]
[[Approval voting|approval]]
```

Adapters should preserve:

- target page title;
- displayed label, if present;
- whether the link was originally an internal/wiki link.

For non-wiki targets, adapters may render internal links as normal hyperlinks, local file links, or unresolved wiki-link markers depending on configuration.

### 7.5 External links

External links use standard MediaWiki syntax:

```mediawiki
[https://electowiki.org/wiki/Approval_voting Approval voting on Electowiki]
https://example.org/
```

Adapters should preserve the URL and label.

### 7.6 Lists

Simple unordered, ordered, and definition-style lists are portable.

```mediawiki
* item
* item
** nested item

# first
# second

; term
: definition
```

Adapters should warn when deeply nested or mixed list constructs cannot be represented faithfully in a target.

### 7.7 Blockquotes

MediaWiki has no single universally beloved blockquote shorthand. The portable profile should support simple HTML blockquote tags when necessary:

```mediawiki
<blockquote>
Quoted material.
</blockquote>
```

Adapters may also support colon-indented text as a legacy wiki quotation/conversation form, but should not assume that all colon-indented lines are semantically blockquotes.

### 7.8 Preformatted text and code

Indented preformatted text is portable enough for many wiki workflows, but explicit tags are clearer for code-heavy documents:

```mediawiki
<pre>
example code
</pre>
```

Inline code should use `<code>` when needed:

```mediawiki
Use <code>mwmap pull</code> to update the local copy.
```

### 7.9 Tables

Simple MediaWiki tables are portable:

```mediawiki
{| class="wikitable"
! Name !! Value
|-
| Foo || Bar
|-
| Baz || Quux
|}
```

Portable tables should avoid:

- nested tables;
- complex rowspans and colspans;
- layout-only tables;
- templates inside structural table syntax;
- CSS-dependent meaning.

Adapters should warn when table features are simplified.

### 7.10 Images and files

MediaWiki file syntax is portable as a semantic image/file reference, but not all targets can preserve every option.

```mediawiki
[[File:Example.png|thumb|alt=Example alt text|Caption text]]
```

Adapters should preserve where possible:

- file name or upstream file ID;
- caption;
- alt text;
- width;
- alignment;
- thumbnail/full-size intent.

Image binaries and attachments should be handled by the upstream adapter and mapping store, not embedded directly in the hub file.

### 7.11 Categories and tags

MediaWiki categories are portable as tags/classification metadata:

```mediawiki
[[Category:Voting methods]]
```

Adapters may map categories to:

- MediaWiki categories;
- Zim tags;
- Org tags;
- Google Docs document properties or comments;
- sidecar metadata;
- no visible output, with warning.

Category handling should be configurable per upstream.

### 7.12 Redirects

Redirects are MediaWiki-native and should be preserved when syncing MediaWiki-to-MediaWiki:

```mediawiki
#REDIRECT [[Target page]]
```

For non-MediaWiki targets, redirects should usually become metadata or a short explanatory page rather than a normal document body.

## 8. Non-portable or risky constructs

The following constructs are allowed in hub files, but they are not fully portable. Adapters must either preserve them, transform them intentionally, or report a warning.

### 8.1 Templates

Templates are central to MediaWiki, but they are site-dependent.

```mediawiki
{{Infobox voting method
| name = Approval voting
}}
```

A template in a hub file should be treated as one of:

1. portable by explicit adapter rule;
2. MediaWiki-specific but raw-preserved;
3. expanded to rendered content with a warning;
4. dropped only under an explicit lossy-export mode.

Adapters must not silently discard templates.

### 8.2 Parser functions and magic words

Parser functions and magic words are not portable unless an adapter has specific knowledge of the construct.

```mediawiki
{{#if:{{{name|}}}|Name: {{{name}}}}}
{{PAGENAME}}
```

These should normally be raw-preserved or flagged as MediaWiki-specific.

### 8.3 Extension tags

Extension tags such as `<ref>`, `<gallery>`, `<math>`, `<syntaxhighlight>`, and others vary in portability.

```mediawiki
<ref>Reference text.</ref>
```

Some extension tags have obvious mappings to other systems. For example, references may map to footnotes, and math may map to LaTeX or MathML. Other tags should be raw-preserved or warned.

### 8.4 Raw HTML

MediaWiki permits limited HTML. Raw HTML should be treated cautiously.

```mediawiki
<span class="example">styled text</span>
```

Adapters should preserve semantic HTML when possible and warn when style-only HTML is lost.

### 8.5 Complex tables

Complex tables are allowed but risky. The hub format does not require all adapters to preserve table layout perfectly.

A table should be considered complex if it uses:

- nested tables;
- extensive inline CSS;
- rowspans or colspans;
- templates as table syntax;
- layout-only structure;
- mixed block content inside cells.

### 8.6 Comments

HTML comments are valid in wikitext and may be used for `mwmap.py` metadata or human notes.

```mediawiki
<!-- FIXME: verify this before pushing to Wikipedia -->
```

Adapters should preserve comments when round-tripping through the hub, but many upstreams cannot display or store comments naturally. Loss of comments should be reported.

## 9. Raw preservation

### 9.1 Purpose

Raw preservation is the mechanism that allows `mwmap.py` to avoid data loss when an upstream construct cannot be represented portably.

The preferred strategy is to preserve the original wikitext construct directly whenever it is already valid MediaWiki wikitext. For example, a template can often remain as a template in the hub file.

When the original construct comes from a non-MediaWiki upstream and has no clean wikitext equivalent, `mwmap.py` may use an explicit raw block.

### 9.2 Raw block syntax

The initial raw block syntax is an XML-like extension tag in the `mwmap` namespace:

```mediawiki
<mwmap-raw format="zim" reason="unsupported-checkbox-state">
Original source fragment goes here.
</mwmap-raw>
```

This tag is not intended for publication. It is a hub-format escape hatch.

If a page containing `<mwmap-raw>` is pushed to a public MediaWiki site, the adapter must either:

- transform it to an acceptable target representation;
- remove it under explicit lossy-export mode;
- refuse the push;
- put it in an HTML comment, if appropriate and configured.

### 9.3 Raw block attributes

Recommended attributes:

- `format`: original source format, such as `zim`, `org`, `gdocs`, `html`, `markdown`, or `mediawiki`;
- `reason`: short reason for raw preservation;
- `upstream`: optional upstream name;
- `id`: optional stable identifier for merge tracking.

Example:

```mediawiki
<mwmap-raw format="gdocs" reason="suggestion-range" upstream="campaign-docs" id="raw-20260622-001">
Google Docs suggestion metadata that cannot yet be represented in portable wikitext.
</mwmap-raw>
```

## 10. Loss reports

Every nontrivial import, export, pull, push, or cross-upstream conversion should be able to produce a loss report.

A loss report should classify issues using a small vocabulary:

```text
ok       preserved without known loss
changed  intentionally normalized or reformatted
warn     preserved imperfectly or target behavior may differ
raw      preserved opaquely
loss     information was lost under explicit lossy mode
fail     conversion or push should not proceed without user action
```

Example report:

```text
warn: table uses colspan; Google Docs export may simplify structure
raw: preserved MediaWiki template {{Infobox voting method}}
changed: first level-1 heading mapped to Google Docs title
fail: unresolved <mwmap-raw format="gdocs"> block would be published to Wikipedia
```

Loss reports may be emitted as text, JSON, or both. The human-readable form should be easy to paste into a commit message or issue.

## 11. Adapter requirements

Each adapter should declare what it can read, write, preserve, and warn about.

An adapter should have a capability profile such as:

```text
adapter: mediawiki
reads: wikitext, page metadata, revision IDs
writes: wikitext
portable: headings, paragraphs, emphasis, links, lists, tables, files, categories
special: templates, parser functions, extension tags, redirects
roundtrip: high when source and target are the same site family
```

or:

```text
adapter: google-docs
reads: document structure, text styles, paragraph styles, tables, inline objects
writes: batch update requests or importable document formats
portable: headings, paragraphs, emphasis, links, lists, simple tables
special: suggestions, comments, named ranges, document styles, sharing metadata
roundtrip: medium; many document-style details are not hub-native
```

or:

```text
adapter: zim
reads: zim page text and notebook-relative links
writes: zim page text
portable: headings, paragraphs, emphasis, links, lists, images, attachments
special: checkboxes, tags, notebook-specific link resolution
roundtrip: high for simple personal notes
```

## 12. MediaWiki adapter

The MediaWiki adapter is a privileged adapter because the hub format is based on MediaWiki wikitext.

The adapter should support:

- pulling page wikitext;
- recording page title, namespace, page ID, revision ID, and timestamp;
- pushing edited wikitext;
- detecting edit conflicts;
- preserving categories, redirects, templates, parser functions, and extension tags;
- optionally using Parsoid for structured analysis, linting, or round-trip checks.

For same-site MediaWiki round-tripping, raw wikitext should normally remain raw wikitext. The adapter should not normalize or prettify wikitext merely because it can.

### 12.1 Parsoid use

Parsoid is useful as conversion machinery, especially when `mwmap.py` needs a structured HTML/DOM view of wikitext or wants to compare rendered structure. It should not replace hub wikitext as the canonical source.

Recommended uses:

- wikitext-to-HTML inspection;
- HTML-to-wikitext conversion for upstreams that provide HTML;
- linting and round-trip testing;
- visual-editor-like transformations;
- detecting constructs that are likely to produce dirty diffs.

## 13. Google Docs adapter

Google Docs should be treated as a structured-document upstream, not as a plain text format.

The adapter should support:

- pulling document content through the Google Docs API or export formats;
- mapping headings, paragraphs, emphasis, links, lists, simple tables, images, and footnotes where possible;
- recording document ID, revision/version information where available, and export timestamps;
- pushing changes through API requests or import/update flows;
- preserving or reporting comments, suggestions, named ranges, document styles, and sharing metadata.

Google Docs-specific collaboration features should normally live in sidecar metadata or loss reports, not in visible hub content.

## 14. ZimWiki adapter

The Zim adapter is important because ZimWiki is a likely local editing environment and may represent the maintainer's personal notebook workflow.

The adapter should support:

- mapping Zim page titles and file paths to hub page identities;
- preserving headings, paragraphs, links, lists, images, and attachments;
- mapping Zim notebook-relative links to wiki links;
- mapping Zim tags to categories or sidecar tags;
- warning about constructs that do not map cleanly to portable wikitext.

Checkboxes and task-list features should be mapped deliberately. For example, a Zim checkbox may become a plain list item, a template, an Org-style TODO marker, or a raw-preserved construct depending on configuration.

## 15. Org adapter

Org is a plausible personal-notes upstream or future replacement for Zim in some workflows. It should be treated as a spoke, not the hub format.

The adapter should support:

- mapping Org headings to wikitext headings;
- preserving paragraphs, emphasis, links, lists, code blocks, tables, and tags;
- mapping TODO states, priorities, planning lines, drawers, and properties either to visible wikitext, sidecar metadata, or raw-preserved blocks;
- warning when executable/literate-programming constructs are not preserved.

Org export features may be useful, but `mwmap.py` should not assume that Org export semantics are the same as hub semantics.

## 16. Markdown, Typst, and LaTeX adapters

Markdown, Typst, and LaTeX are important import/export targets, but they are not the hub format.

### 16.1 Markdown

Markdown is a useful low-friction interchange format. CommonMark is especially useful when predictable parsing is more important than feature richness.

Markdown exports should be allowed to be lossy if the user asks for a simple Markdown view. Loss reports should identify dropped or simplified wiki constructs.

### 16.2 Typst

Typst should be treated primarily as a publishing target. Hub content may be exported to Typst when a document needs attractive typeset output, but Typst should not drive the canonical representation.

### 16.3 LaTeX

LaTeX should be treated primarily as a publishing and math-heavy technical-writing target. It is not a natural representation for wiki links, categories, MediaWiki templates, Google Docs collaboration state, or personal notebook metadata.

## 17. Merge and conflict behavior

The hub format should support Git-like local workflows.

A mapping may have multiple upstreams. For example, one hub page could be associated with:

- an Electowiki page;
- a Wikipedia draft page;
- a Google Doc used for collaboration;
- a local Zim or Org note.

`mwmap.py` should track, at minimum:

- the last pulled upstream revision for each upstream;
- the local hub revision or Git commit;
- whether the hub has unpushed changes;
- whether an upstream has changed since the last pull;
- whether a conversion required warnings, raw preservation, or loss.

The hub text file is the mergeable artifact. Sidecar metadata is the synchronization state.

When two upstreams have changed independently, `mwmap.py` should prefer an explicit conflict over a silent merge that changes meaning.

## 18. Normalization rules

Hub files should be normalized enough to keep diffs readable, but not so aggressively that imported wikitext is churned unnecessarily.

Recommended normalization:

- preserve paragraph boundaries;
- preserve heading levels;
- use UTF-8;
- use LF line endings in the local repository;
- avoid trailing whitespace except where semantically required;
- avoid editor-specific wrapping as a semantic signal;
- keep categories together near the end of MediaWiki-oriented pages, unless the source page intentionally uses another convention.

Avoid automatic normalization of:

- template parameter ordering;
- whitespace inside templates;
- table layout;
- citation/reference formatting;
- comments;
- raw HTML;
- extension tags.

The default should be minimal-diff behavior.

## 19. Validation levels

`mwmap.py` should eventually support validation levels.

### 19.1 Level 0: raw wikitext

The file is MediaWiki wikitext, but no portability claims are made.

### 19.2 Level 1: portable with warnings

The file is mostly portable, but contains constructs that require warnings or adapter-specific handling.

### 19.3 Level 2: portable core

The file uses only the portable profile defined in this document.

### 19.4 Level 3: target-safe

The file is known to be safe for a specific target, such as a particular MediaWiki site, Google Docs collection, Zim notebook, or Org export profile.

A document may be Level 2 in general but not Level 3 for Wikipedia if it contains local conventions unacceptable on Wikipedia.

## 20. Example hub document

```mediawiki
= Example voting method note =

This is a short note about [[Approval voting|approval]] and [[STAR voting]].

== Summary ==

Approval voting allows each voter to approve any number of candidates.

STAR voting uses score ballots and then an automatic runoff between the two highest-scoring candidates.

== Comparison ==

{| class="wikitable"
! Method !! Ballot type !! Runoff?
|-
| Approval || approval ballot || no
|-
| STAR || score ballot || yes
|}

== External links ==

* [https://electowiki.org/wiki/Approval_voting Approval voting on Electowiki]
* [https://electowiki.org/wiki/STAR_voting STAR voting on Electowiki]

[[Category:Voting methods]]
```

## 21. Example non-portable document with preservation

```mediawiki
= Imported draft =

This paragraph is portable.

{{Draft notice|source=Google Docs}}

<mwmap-raw format="gdocs" reason="comment-thread">
Original Google Docs comment thread metadata not yet mapped by this adapter.
</mwmap-raw>

<ref>This reference may map to a footnote in some targets.</ref>

[[Category:Drafts]]
```

This is a *pivot* example, not a MediaWiki mirror — a MediaWiki `.mw` mirror would never contain an `<mwmap-raw>` block. It is valid as a hub pivot file, but it is not portable core. A push to Wikipedia, Electowiki, Zim, or Google Docs should either transform or report the `mwmap-raw` block.

## 22. Command behavior implications

This section is not a full CLI specification, but the hub format implies several command behaviors.

### 22.1 Pull

A pull operation should:

1. read the upstream document;
2. convert it to hub wikitext;
3. update sidecar sync metadata;
4. write or merge the hub file;
5. produce a loss report when relevant.

### 22.2 Push

A push operation should:

1. read the hub file;
2. validate it for the target upstream;
3. refuse or warn on unresolved target-unsafe constructs;
4. convert to the target format;
5. push through the target adapter;
6. update sidecar sync metadata.

### 22.3 Diff

A diff operation should be able to compare:

- hub file versus last pulled upstream state;
- hub file versus current upstream state;
- one upstream's rendered/imported hub form versus another's;
- two generated representations, for debugging only.

### 22.4 Validate

A validate operation should report:

- portability level;
- target safety;
- raw-preserved blocks;
- risky templates or parser functions;
- complex tables;
- comments or collaboration features that may not survive export;
- adapter-specific concerns.

## 23. Versioning

The current profile name is:

```text
portable-wikitext-0.1
```

Future versions may refine:

- raw block syntax;
- sidecar metadata requirements;
- adapter capability declarations;
- validation levels;
- target-specific safe profiles;
- mapping of comments, suggestions, footnotes, categories, tags, and attachments.

A future `portable-wikitext-1.0` should not be declared until there are real round-trip tests across at least MediaWiki, one local notebook format, and Google Docs or a Google Docs substitute.

## 24. Test corpus

The hub format should be validated against a real corpus before it is considered stable.

Recommended initial corpus:

- several Electowiki pages with normal prose, categories, links, and tables;
- several Electowiki or Wikipedia pages with templates and references;
- several personal Zim pages with links, attachments, and tags;
- at least one Org file or subtree if Org support is planned;
- several Google Docs with headings, comments, lists, links, and tables;
- one intentionally difficult document containing nested lists, complex tables, images, footnotes, and comments.

The goal of the test corpus is not perfect conversion. The goal is predictable conversion with explicit warnings.

## 25. Open questions

Several of these are now partly answered — see §26 (candidate core formats) and
§6.2 (no in-file markers in mirror files). They are kept here as the record of
what was open and why.

- Should `mwmap.py` use `.mw`, `.wiki`, or another extension for hub files?
- Should raw preservation use `<mwmap-raw>` tags, HTML comments, sidecar blobs, or a combination?
- Should all synchronization metadata live in TOML/SQLite sidecars, or should simple metadata comments be required in each page?
- How much should Parsoid be required for MediaWiki support versus treated as an optional high-fidelity tool?
- Should Google Docs import/export go through the Docs API, Drive export formats, HTML, Markdown, DOCX, or multiple strategies?
- How should comments and suggestions be represented without polluting public wiki output?
- Should Zim checkboxes map to templates, list syntax, Org-style TODO markers, or sidecar task metadata?
- How should category/tag mapping behave across MediaWiki, Zim, Org, and Google Docs?
- What should the first target-specific safe profiles be: `electowiki-safe`, `wikipedia-safe`, `zim-safe`, `gdocs-safe`, or something else?

## 26. Candidate core formats

The core format is the most consequential choice in this document, because it is
what a maintainer must be able to read by eye — possibly years from now,
possibly without `mwmap.py`. This section records the candidates and the
reasoning so the choice stays auditable even before it is final. It answers the
first cluster of §25's open questions.

### 26.1 Two roles, not one

"Core format" quietly covers two different jobs that should not be merged:

1. **Per-upstream mirror** — the local, byte-faithful copy of what one upstream
   holds. Its job is fidelity and debuggability *for that upstream*. The
   upstream is authoritative, including any post-save transforms it applies, so
   the mirror stores exactly the bytes the upstream serves. After a push,
   `mwmap.py` re-fetches the canonical post-save revision and adopts *that* as
   the new base, rather than assuming the pushed text was stored verbatim.
2. **Neutral pivot** — a single intermediate representation used only when
   bridging two *different* upstreams (MediaWiki ↔ Org, say). Its job is to be a
   common denominator for diff, merge, and mapping.

These have different requirements. A same-system round-trip
(MediaWiki ↔ MediaWiki) needs only role 1, involves no pivot, and should apply
no normalization. A cross-system round-trip needs role 2 — or needs direct
adapter-to-adapter conversion with no stored pivot at all.

The MediaWiki decision is now firm: **the mirror is exact, raw wikitext
(`.mw`), upstream-authoritative, with no mwmap-injected markers in the file.**
This supersedes the earlier "portable wikitext profile" framing *for the
mirror*; the profile material in §7–§9 is best read as describing a candidate
*pivot*.

### 26.2 Selection criteria

- **Eyeball-auditable** as plain text in any editor or pager.
- **Collaboratively edited** in real practice, so the maintainer's skills
  transfer and the format has survival pressure behind it.
- **Durable beyond mwmap** — ideally pinned by a written specification *and* an
  independent conformance suite, not "the spec is whatever one program happens
  to do."
- **High enough fidelity** for the documents actually being synced.
- **Already known, or worth learning.**

### 26.3 The candidates

| Format | Best role | Fidelity | Spec durability | Eyeball-audit |
| --- | --- | --- | --- | --- |
| MediaWiki wikitext (`.mw`) | MW mirror (**chosen**) | High *for MediaWiki* | Implementation-defined (PHP parser + Parsoid) | Good |
| CommonMark / Pandoc Markdown | Neutral pivot | Medium base → high with Pandoc extensions | **Strong** — written spec + conformance suite | Excellent |
| AsciiDoc | Neutral pivot | High out of the box | Improving (AsciiDoc Language spec; Asciidoctor reference) | Good |
| Emacs Org | Personal-notes mirror; possible pivot | High | Implementation-defined (`org-element`) | Excellent |
| reStructuredText | Pivot | High | Implementation-defined (docutils) | Fair |
| Parsoid HTML / pagebundle | MW bridge (machinery only) | **Highest for MediaWiki** | HTML is well-specified | Poor (verbose) |
| LaTeX / TeX | Publishing target | Very high (esp. math) | Stable, but a macro language, not a document model | Poor without expansion |
| Typst | Publishing target | High | Young, single implementation | Good |
| Djot | Future pivot | Medium-high (native attributes/divs/spans) | Clean spec (CommonMark/Pandoc author) | Excellent |
| OOXML `.docx` / ODF `.odt` | Excluded | High | Heavily specified | **Fails** (zip-of-XML) |

Notes on the ones that matter most:

- **MediaWiki wikitext** is the right *mirror*, but a poor *neutral pivot*: its
  semantics are site-bound (templates, parser functions, magic words expand
  differently per wiki) and it has no spec independent of the parser. That is
  fine when the upstream is authoritative anyway, and weak when the format is
  meant to outlast every tool that reads it.
- **Pandoc Markdown / CommonMark** is the durability front-runner: CommonMark is
  the only candidate here whose correctness is pinned by a written spec *and* a
  public conformance suite. Base CommonMark is too thin (no tables, footnotes,
  definition lists, attributes), so a real pivot would use Pandoc's
  extensions — which buys fidelity at some cost to the single-spec guarantee.
- **AsciiDoc** is "semantic Markdown": higher fidelity out of the box (tables,
  cross-references, admonitions, attributes) with a maturing language spec, but
  a smaller ecosystem.
- **Emacs Org** is the highest-fidelity readable format the maintainer already
  lives in, and the natural raw mirror for a personal-notebook spoke; its
  Org-specific semantics (agenda, babel) simply don't map outward.
- **Parsoid HTML** is the genuinely lossless MediaWiki interchange — but it
  fails the eyeball-audit test, so it belongs *inside* conversion machinery,
  never as the canonical surface.
- **LaTeX and Typst** are publishing *targets* (§16), not hubs: both are
  typesetting/macro languages where reading the source doesn't tell you the
  output without expansion. (Math fragments can still ride along inside
  `<math>`.)
- **OOXML / ODF** are not eyeball-auditable even unzipped — text runs are split
  mid-sentence by formatting and buried under relationship XML. Don't learn to
  read them raw; convert with Pandoc and audit the *converted* form.

### 26.4 Recommendation

- **Keep raw `.mw` as the MediaWiki mirror.** The reasons are sound: the
  upstream is authoritative, wikitext has decades of compatibility pressure, it
  is the native source of the most important initial upstreams, and there is
  direct rapport with core MediaWiki developers to lean on when the format
  bites.
- **Store other upstreams raw in their own native format too** — Org for an
  Emacs notebook spoke, for example — so the per-upstream-mirror principle stays
  uniform and every mirror is debuggable in the format its upstream actually
  speaks.
- **Do not pick a single neutral pivot yet.** Until a concrete cross-system need
  exists, prefer direct adapter A→B conversion (Pandoc and Parsoid as
  machinery) over inventing a stored pivot. When a pivot becomes unavoidable,
  the readable front-runners are Pandoc-flavored Markdown (best durability) and
  AsciiDoc (best out-of-the-box fidelity); keep Parsoid HTML strictly inside the
  machinery as the lossless-but-unreadable MediaWiki bridge.
- **Litmus test for "outlive mwmap":** prefer formats pinned by a written spec
  and an independent conformance suite. Only CommonMark clearly clears that bar
  today, with the AsciiDoc Language spec maturing toward it. That argues for a
  Markdown/AsciiDoc *pivot*, not against a raw-wikitext *mirror* — the mirror's
  authority comes from the upstream, so implementation-defined wikitext is
  acceptable there.

### 26.5 Further reading

- CommonMark specification and test suite: <https://spec.commonmark.org/>
- AsciiDoc Language documentation: <https://docs.asciidoctor.org/asciidoc/latest/>
- Org mode (`org-element` is the de facto parser): <https://orgmode.org/>
- Djot: <https://djot.net/>

## 27. References

These references inform the adapter architecture. They are not incorporated as normative dependencies of the hub format.

- MediaWiki Parsoid: <https://www.mediawiki.org/wiki/Parsoid>
- Pandoc User's Guide: <https://pandoc.org/MANUAL.html>
- Pandoc filters and AST overview: <https://pandoc.org/filters.html>
- Google Docs API document structure: <https://developers.google.com/workspace/docs/api/concepts/structure>
- Google Docs API structural edit rules: <https://developers.google.com/workspace/docs/api/concepts/rules-behavior>
- Google Docs API documents resource: <https://developers.google.com/workspace/docs/api/reference/rest/v1/documents>

