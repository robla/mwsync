# Preview Workflow

`mwsync.py preview` renders a local `.mw` working file through the configured
wiki's MediaWiki parser without saving anything to the wiki.

## Status Quo

The current preview command is intentionally simple:

```bash
mwsync.py preview Maine
mwsync.py preview Maine --open
mwsync.py preview Maine --output /tmp/Maine-preview.html
```

It resolves `Maine` through `mwsync.yaml`, reads the configured local file, and
sends that wikitext to the configured `wiki.api_base` using the Action API
`action=parse`. The generated HTML is written to:

```text
_cache/<Article_Key>/preview.html
```

The output includes a small local preview banner, the generated timestamp, the
source `.mw` path, and a link to the real wiki page. Links and image/resource
URLs that are root-relative are rewritten to the target wiki host so the local
file is more useful in a browser.

This is a network operation, but it is read-only. It does not log in, does not
request an edit token, and does not save the page.

## Limitations

This is not identical to pressing "Show preview" in the Electowiki edit form.
It uses the same parser endpoint, but it is displayed in a local HTML wrapper
rather than inside the live site skin with the user's browser session,
gadgets, preferences, or edit form.

The command also does not currently:

- inject local CSS or JavaScript from the live wiki skin;
- submit text into the browser's edit form;
- preserve browser session state;
- compare the preview against the cached upstream revision;
- preview multiple tracked pages at once.

## Near-Term Plan: Preview the Pushed Content

A local web server is useful, but it is not what makes the preview meaningfully
closer to "preview before pushing." The important semantic improvement is to
preview the exact content that `push` would submit, rendered the way the wiki
will render it after saving.

The decided workflow keeps three small, single-purpose commands and adds no new
options to make the common case work:

```bash
mwsync.py commit Maine -m "Update Maine"
mwsync.py preview Maine
mwsync.py push Maine
```

`commit` is an offline operation: it snapshots the working file to
`_cache/<Article_Key>/commit.mw` without touching the network. `push` submits
that snapshot, not the mutable working file. So the only change needed to make
`preview` faithful is to align its source text and parser call with `push`.

### Source text: prefer the pending commit

When `_cache/<Article_Key>/commit.mw` exists, `preview` should read that pending
commit snapshot instead of the mutable working file. That makes preview match
the push path exactly: the user previews the same bytes `mwsync.py push` will
submit. When no pending commit exists, `preview` falls back to the working
`.mw` file as today.

This single rule supports both placements the user might want — reviewing right
after `commit` (a tight `commit` -> `preview` -> `commit --amend` loop) and a
final check right before `push` — because both read the same snapshot. A
`--working` flag to force previewing the editable `.mw` file is the one escape
hatch worth adding; it is optional and not required for the default flow.

Deliberately *not* folding preview into `commit` or `push`: `commit` must stay
offline (preview is a network parse call), and a `push --preview` gate would be
a late, redundant option once the standalone `preview` step already reads
`commit.mw`. Keep the commands separate and let the user place `preview` where
they want it.

### Parser call: apply the pre-save transform

The current preview calls `action=parse` with the raw wikitext and no
pre-save transform. MediaWiki applies the pre-save transform (PST) at *save*
time, so the wikitext `push` ultimately stores is the post-PST text: `~~~~`
signatures expanded, `{{subst:...}}` substituted, trailing whitespace
normalized. Rendering without PST therefore diverges from the saved result for
any page using those constructs.

`preview` should pass `pst=1` to `action=parse` so the rendered HTML reflects
what the page will actually look like once saved. This is also what the wiki's
edit-form "Show preview" does. For typical article edits with no signatures or
`subst`, `pst=1` changes nothing, so it is a safe default.

### Fidelity ceiling

Even previewing `commit.mw` with `pst=1` is not byte-identical to the page after
saving. The saved revision gets a real revid, real `{{REVISIONID}}` /
`{{REVISIONUSER}}` / timestamp values, and is rendered in the context of a page
that actually exists — affecting self-referential category membership, red vs.
blue links, and `create_new` pages in particular. Previewing the pending commit
with PST closes most of the gap; the remainder is inherent to content that has
not been saved yet.

## Future Directions

A more advanced workflow could add an edit-form integration mode, such as:

```bash
mwsync.py preview --edit Maine
```

That mode could open the real Electowiki edit page and help transfer local
wikitext into the browser preview flow. It should be designed carefully because
browser cookies, login state, CSRF tokens, and user preferences belong to the
browser session, not to `mwsync.py`.

Another possible direction is a richer local preview page that loads target-wiki
stylesheets, shows parser warnings more prominently, and displays metadata such
as base revid, pending commit state, and local modification status.

### Secure Transient Local Server

Serving the generated HTML over loopback HTTP is a display and browser-security
improvement over opening a `file://` URI. It does not by itself make the preview
more like Electowiki's edit-form preview; the parser call and the choice of
source text determine that. It does, however, provide a cleaner foundation for
opening previews in a browser.

Instead of introducing third-party dependencies like Flask, this server can be
implemented directly using Python's standard `http.server` and `socketserver`
libraries. It should be hardened:

1. **In-memory only:** Serve exactly one generated HTML document from memory.
   The request handler must never derive a filesystem path from the request, so
   `_cache/`, the repository, and the working directory are structurally
   unreachable — there is no file mapping to traverse.
2. **Loopback-only binding:** Bind strictly to `127.0.0.1`, not `localhost`
   and not `0.0.0.0`.
3. **Randomized port:** Bind to port `0` so the operating system allocates an
   ephemeral, conflict-free port.
4. **Access token:** Generate a cryptographically secure token (e.g.
   `secrets.token_urlsafe(32)`) and put it in the path, such as
   `http://127.0.0.1:PORT/preview/TOKEN`. Compare the path token against the
   expected value with `secrets.compare_digest`, not `==`, to avoid a timing
   oracle.
5. **Exact-path, GET-only:** Serve only the exact `/preview/TOKEN` path and only
   `GET` (and `HEAD`). Respond `404` to any other path — including
   `/favicon.ico` — and `405` to any other method. This keeps the inert viewer
   from being poked with `POST` or path probing.
6. **Validate the Host header:** Require `Host: 127.0.0.1:PORT` and reject
   anything else. With an ephemeral port and a secret token this is already
   near-impossible to reach, but it is the cheap defense against DNS-rebinding
   from a page the user happens to have open in the same browser.
7. **Strict response headers:** Serve with an explicit
   `Content-Type: text/html; charset=utf-8`, plus `X-Content-Type-Options:
   nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and a
   restrictive Content Security Policy. A reasonable starting point is
   `default-src 'none'; img-src https: data:; style-src 'unsafe-inline';
   base-uri 'none'; form-action 'none'; script-src 'none'`. Dropping JavaScript
   means collapsible tables, reference tooltips, and gadgets will not be
   interactive; that is the intended inert-viewer tradeoff, not a bug.
8. **Short lifetime:** Use a short wall-clock timeout and shut down after
   serving the valid preview route, bounding the lifetime regardless of how many
   requests arrive. Do not shut down after the first request blindly, because
   browser requests for `/favicon.ico` or prefetches could consume it.
9. **No proxying or side effects:** Do not proxy arbitrary requests to
   Electowiki or other hosts, and never perform any action (re-parse, fetch,
   edit) in response to a request. The server only hands back one prepared
   document.

**CSP and the `<base>` element.** The current preview HTML emits
`<base href="...">` to resolve relative URLs against the wiki host, but CSP
`base-uri 'none'` blocks the `<base>` element, so the browser ignores it and any
URL not already absolutized breaks. Resolve this by fully absolutizing `href`
and `src` URLs in the generated document (the existing `_absolutize_preview_urls`
helper already does this for root-relative URLs) and dropping `<base>`
altogether, keeping the strict `base-uri 'none'`.

The main risk is accidentally turning preview HTML into an active local web app
that can execute scripts or interact with other localhost services. The server
should be a narrow, inert document viewer.
