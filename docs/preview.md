# Preview Workflow

`mwsync.py preview` renders draft wikitext through the configured wiki's
MediaWiki parser without saving anything to the wiki. The goal is to make it
easy to review the exact content that is about to be pushed, while keeping local
mwsync state correct after any on-wiki preview or save.

## Current Behavior

The current command supports an interactive browser workflow and file-only
fallbacks:

```bash
mwsync.py preview Maine
mwsync.py preview Maine --link
mwsync.py preview Maine --output /tmp/Maine-preview.html
mwsync.py push --preview Maine
```

It resolves the article through `mwsync.yaml`, prefers
`_cache/<Article_Key>/commit.mw` when a pending commit exists, otherwise reads
the local `.mw` file, calls the configured Action API endpoint with
`action=parse&pst=1`, and writes:

```text
_cache/<Article_Key>/preview.html
```

In an interactive terminal, `mwsync.py preview ARTICLE` serves that HTML through
a short-lived `127.0.0.1` server, opens the browser, and if a pending commit
exists waits for Enter before attempting verified reconciliation. `--link` and
`--output` write the HTML and exit without serving or blocking.

Preview rendering is read-only network activity. It does not log in, request an
edit token, or save the page. `mwsync.py push --preview ARTICLE` renders the
pending commit first, asks for approval, and then performs the normal
authenticated push if approved.

## Design Goal

The common review-and-save workflow should be:

```bash
mwsync.py commit Maine -m "Update Maine"
mwsync.py preview Maine
```

`commit` snapshots the editable working file to
`_cache/<Article_Key>/commit.mw`. `push` submits that pending commit snapshot,
not the mutable working file. Therefore `preview` should prefer the pending
commit when one exists, so the rendered preview matches the content `push` would
submit.

In the default interactive mode, `preview` opens the rendered page, lets the
user review or save in the browser, then waits in the terminal. Pressing Enter
runs verified reconciliation: if the browser save landed, local mwsync state is
fast-forwarded; if not, the pending commit is left intact. This makes `preview`
the normal reminder to clean up after an on-wiki save.

If no pending commit exists, `preview` falls back to the working `.mw` file and
does not offer commit reconciliation.

## Parser Fidelity

`preview` should call `action=parse` with `pst=1`. MediaWiki applies the
pre-save transform when saving, so a faithful preview should show the post-PST
rendered result. This affects signatures, `{{subst:...}}`, and save-time
normalization. For ordinary article edits, `pst=1` usually changes nothing.

This still has a fidelity ceiling. Unsaved content does not yet have a real
revision id, timestamp, revision user, or final saved-page context. Previewing
the pending commit with PST closes most of the gap, but it is not byte-identical
to rendering the saved revision after the edit lands.

## Preview Page

The page is laid out for the common task: copy the local wikitext, open the live
editor, paste, and eyeball the rendered result. The actionable controls come
first, so the default `mwsync.py preview` needs no scrolling and no extra
options to be useful.

The page should be ordered:

1. An action panel at the top containing, in this order:
   - an "Open source editor" link to the live wiki edit form;
   - a **Copy wikitext** button that copies the full source field in one click;
   - the edit summary from the pending commit, when available;
   - a read-only source field with the exact pre-PST wikitext that would be
     submitted or pasted.
2. The rendered HTML from `action=parse&pst=1` below the action panel, as
   reference to scroll through.

Secondary metadata (source path, generated time, live page URL, base revid)
belongs in a collapsed details block inside the action panel, not above the
controls.

The **Copy wikitext** button uses a tiny inline script (`navigator.clipboard`
with an `execCommand` fallback) so the user does not have to hand-select the
field. The script is pinned by its sha256 in the CSP (see "Secure Transient
Local Server").

The source editor link should target the wikitext editor and prefill the summary
where MediaWiki supports it:

```text
https://electowiki.org/w/index.php?title=Talk:Software&action=edit&summary=<url-encoded summary>
```

MediaWiki does not provide a safe URL parameter for injecting arbitrary
wikitext into the edit box. The body still travels by the copy button.

## On-Wiki Save And Reconciliation

It is acceptable for the user to preview in the live Electowiki edit form and
click **Save changes** there. That is a manual on-wiki save, not an API push by
mwsync. The default `mwsync.py preview ARTICLE` terminal prompt should reconcile
this case immediately after browser review. `mwsync.py push ARTICLE` should use
the same reconciliation logic if the user exits preview and reconciles later.

Before submitting from `push`, or before claiming cleanup from `preview`, the
command should fetch the latest upstream revision and compare it to the pending
commit. It may fast-forward local state without submitting an edit only when
both checks pass:

- The latest upstream wikitext matches the pending commit body after the same
  narrow normalization MediaWiki applies on save, such as trailing whitespace
  and final-newline normalization.
- The latest upstream revision's `parentid` equals the pending commit's
  `base_revid`, so the saved revision is a direct child of the revision the user
  reviewed.

When both checks pass, the command should treat the edit as already saved
upstream: cache the canonical saved revision, advance `refs/upstream`,
`refs/base`, and `refs/last-pushed`, clear the pending commit, update push
metadata in `mwsync.yaml`, and report that no edit was submitted.

After this fast-forward, the working file should be rewritten to the canonical
saved text only if it still matches the pending commit body. If the user has
continued editing the working file since `commit`, leave it untouched so those
new edits remain visible as local modifications against the new base.

If either check fails, the command must not claim success and must not overwrite
local work. If no matching browser save is found, it should say so and leave the
pending commit intact. If upstream diverged, it should point the user toward the
normal `fetch` / `merge` reconciliation path.

## Push With Preview

`mwsync.py push --preview ARTICLE` is a useful alternative for a fully
mwsync-managed write:

```bash
mwsync.py commit Maine -m "Update Maine"
mwsync.py push --preview Maine
```

In this mode, `push` renders the pending commit, opens the preview, waits for
explicit approval, and then performs the normal authenticated API push. This
keeps the final write inside mwsync and preserves the usual `base_revid` edit
conflict protection and post-push cache updates.

This is an alternative to the default preview-and-reconcile workflow, not a
replacement. It is useful when the user wants the CLI to perform the final write
rather than saving from the browser edit form.

## Reconciliation Trigger

The loopback server and the terminal prompt are the same `mwsync.py preview`
process. The server runs on a background thread only to hand the page to the
browser; the main thread stays in the foreground and owns reconciliation.
Reconciliation is driven from the terminal, never from the browser.

A browser "Done" button is therefore rejected. A mutating browser endpoint would
turn the inert preview server into a control plane over local state (see "Secure
Transient Local Server"), and would force the server to stay alive for the whole
browser detour. The terminal is the safe control point.

So in the default serve-and-open flow, `preview` blocks before exiting:

```text
Preview opened at http://127.0.0.1:PORT/preview/TOKEN
After saving in the browser, press Enter to reconcile, or Ctrl-C to leave the
pending commit unchanged.
```

The key property: this prompt is reached no matter what happens in the browser —
the save succeeds, the save fails, the browser crashes after **Save changes**, or
the user never saves at all. The browser is never required to report back. On
Enter, the CLI runs the same verified reconciliation as `push` (fetch upstream;
require normalized-text match **and** `parentid == base_revid`):

- If the on-wiki save landed, it fast-forwards local state and reports the saved
  revision.
- If nothing matching is found — the save failed, was abandoned, or was never
  attempted — the pending commit is left intact, and the user can run
  `mwsync.py push` later to submit it.

Because reconciliation verifies rather than assumes, every browser outcome is
safe: the prompt cannot clear a pending commit that was not actually saved, and
cannot advance refs to a revision that does not exist.

The server's short lifetime (below) is independent of this block. The server may
shut down as soon as it has served the page — the loaded page needs no further
server contact, and the edit link points at Electowiki — while the terminal
prompt keeps the process alive until the user responds.

`--link` and other non-interactive invocations do not block; they write the
preview, print the link, and exit, leaving reconciliation to a later
`mwsync.py push`.

## Secure Transient Local Server

Serving preview HTML over loopback HTTP is a display and browser-security
improvement over `file://`. It does not by itself make the preview more
semantically faithful; the source text and parser call do that. It does provide
a better browser origin for response headers and possible future clipboard
support.

The server should be inert:

1. Serve exactly one generated HTML document from memory. Never map request
   paths to local files.
2. Bind strictly to `127.0.0.1`, not `localhost` and not `0.0.0.0`.
3. Use an ephemeral OS-assigned port.
4. Generate a `secrets.token_urlsafe(32)` token and place it in the path, such
   as `/preview/TOKEN`; compare with `secrets.compare_digest`.
5. Serve only the exact tokenized path with `GET` and `HEAD`; return `404` or
   `405` for everything else.
6. Validate `Host: 127.0.0.1:PORT` to reduce DNS-rebinding exposure.
7. Send `Content-Type: text/html; charset=utf-8`,
   `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
   `Cache-Control: no-store`, and a restrictive CSP.
8. Use a short wall-clock timeout and shut down after serving the valid preview
   route; do not shut down after the first request blindly because browsers may
   request `/favicon.ico`.
9. Do not proxy requests, re-parse pages, fetch remote content, edit the wiki,
   or mutate local mwsync state in response to browser requests.

The CSP is:

```text
default-src 'none'; img-src https: data:; style-src 'unsafe-inline';
base-uri 'none'; form-action 'none'; script-src 'sha256-...'
```

`script-src` pins only the sha256 of the tiny copy-to-clipboard helper. The
server computes that hash from the exact script bytes it embeds, so the hash and
the served script cannot drift. Because the policy is hash-pinned rather than
`'unsafe-inline'`, any other inline script — including anything in the rendered
wiki HTML — is still blocked. The copy helper uses no inline event handlers
(which a hash cannot cover); it attaches its listener from the pinned block.

The generated document should not use a `<base>` element. Fully absolutize
`href` and `src` URLs instead, then keep `base-uri 'none'`.

## Security Notes

The local preview contains unpublished draft text. The loopback server protects
against casual network exposure and accidental filesystem serving, but it does
not protect against malicious browser extensions, local malware, or the user
copying draft text to the system clipboard.

Any metadata inserted outside MediaWiki-rendered HTML must be escaped carefully,
including page titles, local paths, edit summaries, URLs, and generated status
messages. Rendered wiki HTML should be treated as untrusted active content:
allow it to display, but use CSP to block form submission and every script
except the one hash-pinned copy helper.
