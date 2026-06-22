# Preview Design

`mwm preview` should behave like legacy `mws preview`: render local or staged
wikitext through the target MediaWiki parser, show the result in a browser, and
help reconcile the local state if the user saves the text manually from the
browser.

`mwm` is a local alias for `mwmap.py`; `mws` is a local alias for `mwsync.py`.
Examples use `mwm`, but the implemented command name should also read naturally
as `mwmap.py preview`.

## Command Shape

```sh
mwm preview [PATH]
mwm preview [PATH] --output preview.html
mwm preview [PATH] --link
mwm preview [PATH] --open
```

`PATH` limits preview to one paired page. If omitted, preview should require
exactly one page mapping or one pending commit; it should refuse ambiguity
rather than opening many browser tabs.

`--output` writes a durable HTML file instead of the default cache location.
`--link` prints a `file://` link and does not start the transient local server.
`--open` opens the generated file in a browser when used with `--output` or
`--link`.

## Source Selection

Preview should choose the source in this order:

1. Pending commit: `_mwmap/cache/<remote>/pages/<pageid>/commit.mw`, with
   metadata from `commit.json`.
2. Working file: the mapped `local_path` from `_mwmap/config.yaml`.

This mirrors `mws preview`: if a pending commit exists, preview shows what
`push` would publish, not whatever happens to be in the working tree. If no
pending commit exists, preview shows the current working file. Missing local
files should produce an actionable error, for example: run `mwm merge PATH` or
restore/create the file first.

## Rendering

Preview should call MediaWiki `action=parse` against the mapping's remote:

```text
action=parse
format=json
formatversion=2
title=<remote title>
text=<source wikitext>
contentmodel=wikitext
prop=text|displaytitle|categorieshtml
disableeditsection=1
pst=1
```

The title should come from pending metadata when previewing a pending commit,
otherwise from the mapping's `remote_path`. The first version can support only
`format: mw`; later versions may preview converted Org/Markdown/Zim content
after conversion to wikitext.

Rendered relative `href` and `src` URLs should be absolutized against the
remote site root so the preview page is useful outside the wiki.

## Preview HTML

The generated page should include:

- Rendered MediaWiki HTML and categories.
- The exact source wikitext in a read-only textarea.
- A "Copy wikitext" button.
- A link to the remote wiki source editor with the edit summary prefilled when
  previewing a pending commit.
- Metadata: remote name, pageid, title, local path, base revid, source kind
  (`pending commit` or `working file`), generated timestamp, and output path.

Default output should be:

```text
_mwmap/cache/<remote>/pages/<pageid>/preview.html
```

That file is disposable cache output. It can be regenerated and should not be
treated as durable project state.

## Browser Serving

Interactive preview should follow the safer `mws preview` model:

1. Write `preview.html`.
2. Start a loopback-only HTTP server on `127.0.0.1` with an unguessable token
   path such as `/preview/<token>`.
3. Open the URL in the default browser.
4. Serve the preview once, or expire after a short timeout.

The server should reject unexpected paths, unexpected `Host` headers, and
non-GET/HEAD requests. It should send conservative headers:

```text
Content-Security-Policy: default-src 'none'; img-src https: data:; style-src 'unsafe-inline'; ...
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Cache-Control: no-store
```

The only inline script should be the copy-to-clipboard helper, pinned by hash in
the CSP as `mwsync.py` does.

## Reconciliation

After opening an interactive preview, `mwm preview` should wait for the user:

```text
After saving in the browser, press Enter to reconcile
(Ctrl-C leaves preview proposal unchanged).
```

On Enter, preview should fetch the latest remote revision and compare it with
the preview proposal:

- If no base revision is known and this is not a new-page proposal, leave state
  unchanged.
- If the latest remote revision is still the base revision, leave state
  unchanged.
- If the latest revision's parent is not the expected base, warn that the remote
  diverged and tell the user to run `mwm pull`.
- If the latest revision is parent-compatible, record it as saved: cache the new
  revision, advance `base_revid`, update the working file if it still matches
  the proposal, and clear the pending commit.

This keeps the useful legacy workflow: a user can preview, open the wiki editor,
paste/copy the exact wikitext, save manually, then let the tool notice and
adopt the new remote revision without submitting a second edit.

## Implementation Notes

Most of the implementation should be adapted from `mwsync.py` preview helpers:

- `_parse_wikitext_preview`
- `_absolutize_preview_urls`
- `_preview_document`
- `_serve_preview_document`
- `_open_transient_preview`
- `_preview_proposal`
- `_reconcile_saved_preview_proposal`

Do not copy legacy key-based paths. Re-home paths through `workspace.py` helpers
and identify pages by `(remote, pageid)`.
