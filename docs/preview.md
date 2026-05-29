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

## Browser Preview and Edit-Form Handoff

This is the planned default UX for `preview`. The page has two jobs: render the
pending content locally so the user can see it, and hand the exact wikitext to
Electowiki's real source editor so the final "Show preview" and save happen in
the live site — with the user's session, skin, and gadgets.

### Default: serve and open

`mwsync.py preview Maine` with no flags should:

1. Render the pending content (the `commit.mw` snapshot when present, else the
   working file) via `action=parse` with `pst=1`.
2. Build the single self-contained HTML document described below.
3. Start the loopback server (see "Secure Transient Local Server") and open the
   tokenized URL in the browser.

Serving over `http://127.0.0.1` rather than `file://` is not merely cosmetic: a
loopback HTTP origin is a browser "secure context," which the clipboard copy
button depends on, and it lets the server set real response headers. If opening
a browser fails (headless or remote session), print the tokenized URL and keep
the server alive for its normal timeout rather than erroring out.

### Writing without opening

A switch writes the HTML to `_cache/<Article_Key>/preview.html` and prints a
clickable `file://` link *without* starting the server or launching a browser —
for remote sessions, scripting, or simply preferring to click the link yourself.

Recommended name: **`--link`** (you get a link to click). Alternatives
considered: `--no-browser`, `--write-only`. Do not call it `--dont-open`.

Under `file://` the page is degraded but still useful: with no HTTP origin there
are no response headers, so any CSP must come from a `<meta http-equiv>` tag, and
the clipboard button falls back to `document.execCommand('copy')` (some browsers
will require a manual select-and-copy). The rendered preview and the edit-form
link work unchanged.

### The preview page

Top to bottom, the document contains:

1. The local-preview banner and metadata (source path, generated time, link to
   the live page, base revid, and pending-commit status).
2. The rendered HTML, from `action=parse` with `pst=1`.
3. A **Source wikitext** panel: a large `<textarea>` holding the *exact bytes
   that `push` would submit* — the `commit.mw` snapshot, verbatim and
   pre-transform (this is what gets pasted; MediaWiki applies the pre-save
   transform itself on save). A **Copy to clipboard** button sits beside it.
4. The **edit summary**, shown as plain text so the user can read it.
5. A prominent **Open Electowiki source editor** link and short step-by-step
   instructions.

Note the deliberate split: the rendered HTML (item 2) shows the *post-PST*
result so the user sees what saving will produce, while the textarea (item 3)
holds the *pre-PST* wikitext, because that is what actually gets pasted and
saved.

### Edit-form handoff

The handoff link targets the wiki's source editor with the summary prefilled:

```
https://electowiki.org/w/index.php?title=Talk:Software&action=edit&summary=<url-encoded summary>
```

- `action=edit` (not `veaction=edit`) opens the wikitext source editor, not the
  visual editor.
- MediaWiki prefills the edit-summary box from the `summary` URL parameter, so
  the commit message is carried across automatically. Summaries cap at 500
  characters, well within URL limits.
- MediaWiki has no URL parameter to inject arbitrary wikitext into the edit box
  (by design), so the body still travels via the clipboard. `preload` only loads
  existing on-wiki pages and does not apply here.

Workflow:

1. Review the rendered preview.
2. Click **Copy to clipboard**.
3. Click **Open Electowiki source editor** — the summary is already filled in.
4. Select-all in the edit box and paste, replacing its contents.
5. Use the live "Show preview" to confirm in the real skin, then save.

### ChatGPT's objection to bypassing mwsync.py push

Saving from the browser editor after the handoff is convenient, but it bypasses
the core `mwsync.py push` safety model. `push` submits the pending commit
snapshot through the Action API using the recorded `base_revid`, letting
MediaWiki detect edit conflicts against the revision the user actually reviewed.
After a successful save, `push` also refreshes the local cache, advances
`refs/upstream`, `refs/base`, and `refs/last-pushed`, clears the pending commit,
and records push metadata in `mwsync.yaml`.

A manual browser save does not update that local state. It may leave mwsync
believing a pending commit still needs to be pushed, or leave local refs pointing
at the pre-save revision until the user manually fetches and reconciles. It also
moves the final write outside the exact code path that was designed to keep the
working file, pending commit, cache, and wiki revision aligned.

The safer default is therefore:

```bash
mwsync.py commit Maine -m "Update Maine"
mwsync.py push --preview Maine
```

In that design, `push --preview` renders the pending commit, opens the preview,
waits for explicit approval, and then performs the normal `mwsync.py push`
operation itself. Browser edit-form handoff should remain available as a manual
escape hatch, but saving from the browser should be documented as switching
from the mwsync-managed push path to a manual wiki edit.

### Claude's response: why push should allow an on-wiki save

The objection above is mechanically accurate but draws the wrong conclusion. Two
observations change the prescription.

First, an on-wiki save diverges only the *bookkeeping*, not the *data*. After the
user pastes the clipboard body and saves, the working file, `commit.mw`, and the
new wiki revision all hold the same bytes. Only mwsync's refs are behind. Second,
"upstream moved out from under local state" is not a safety breach — it is the
exact condition the git-like fetch/merge/refs model exists to handle. A browser
save is just another way upstream can advance, no different in kind from someone
else editing the page.

So the conclusion "discourage the browser save; route everything through
`push --preview`" solves the problem by asking the user not to do the thing they
have said they will habitually do — review in the live edit form and click
**Save changes** while it is right in front of them. The tool should not fight
that muscle memory. The better fix keeps the on-wiki save first-class and makes
`push` reconcile after it.

**Make `push` idempotent.** When `push` runs, it should first fetch upstream and
compare the latest revision to the pending commit body:

- If they match, the wiki already has this exact edit. `push` does *not*
  re-submit; it advances `refs/upstream`, `refs/base`, and `refs/last-pushed`,
  clears the pending commit, records push metadata, and reports something like
  `already saved upstream as r19779 (no edit submitted)`. This is the same
  bookkeeping `push` always performs — it simply recognizes that the write
  already happened in the browser.
- If they do not match — the user tweaked the text in the edit box, or the
  pre-save transform expanded a `~~~~` or `{{subst:}}` — `push` reports the
  divergence and points at `fetch` / `merge`, the ordinary upstream-moved path.
  Nothing is overwritten.

The comparison must be normalized, not byte-exact: MediaWiki strips trailing
whitespace and forces a single final newline on save, so even an otherwise
untouched page can come back differing in trailing bytes. Normalizing trailing
whitespace before comparing avoids spurious "diverged" reports for the common
case.

This matters because of what `push` does *today*: `run_push` submits the pending
commit against the stored `base_revid` without re-checking upstream first, so a
push after an on-wiki save currently fails with a spurious edit conflict. The
idempotent behavior turns that failure into a clean fast-forward and is a
required code change, not just documentation.

The user's habitual flow therefore stays intact and ends in aligned state:

```bash
mwsync.py commit Maine -m "Update Maine"
mwsync.py preview Maine          # review, copy, open the edit form, Save changes
mwsync.py push Maine             # fast-forwards local refs to the saved revision
```

`push --preview` remains worth offering as an *alternative* for users who prefer
the CLI to perform the write, but it should not be positioned as the safer
default. With idempotent reconciliation, the on-wiki save is not a bypass of the
safety model — it is a supported write path that `push` cleans up after.

### Summary handoff: URL parameter, not a wikitext comment

An earlier sketch appended the commit message to the wikitext as a trailing
`<!-- ... -->` comment so it would travel with the body in the clipboard. That
works, but it has a footgun: if the user forgets to strip the comment before
saving, it is stored into the article, and it forces a manual "copy the summary
out of the comment" step. Carrying the summary in the `&summary=` URL parameter
instead keeps the clipboard body clean (exactly the bytes `push` would submit)
and removes both the cleanup and the risk. The comment approach is kept only as
a fallback for a wiki or link path where a prefilled summary does not survive.

### Clipboard button and CSP

The copy button is the one element that requires JavaScript, which conflicts
with the strict `script-src 'none'` policy. Allow exactly one inline script,
pinned by hash or nonce (e.g. `script-src 'sha256-...'`), implementing only the
copy action: attempt `navigator.clipboard.writeText(...)` (works in the
localhost secure context) and fall back to selecting the textarea and
`document.execCommand('copy')`. A hash- or nonce-pinned inline script that ships
with `mwsync` is still inert to injection — no remote code loads, and
wiki-supplied HTML in the rendered region still cannot execute. The rest of the
CSP is unchanged.

## Secure Transient Local Server

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
   base-uri 'none'; form-action 'none'; script-src 'sha256-...'`, where the
   `script-src` hash pins the single inline copy-button script (see "Clipboard
   button and CSP" above); use `script-src 'none'` if the copy button is
   dropped. Wiki-supplied HTML in the rendered region still cannot execute, so
   its collapsible tables, reference tooltips, and gadgets will not be
   interactive — that is the intended inert-viewer tradeoff, not a bug.
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
