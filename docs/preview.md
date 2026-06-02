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
a short-lived `127.0.0.1` server, opens the browser, and waits for Enter before
attempting verified reconciliation. This should work whether the preview source
is a pending commit or the uncommitted working file. `--link` and `--output`
write the HTML and exit without serving or blocking.

Preview rendering is read-only network activity. It does not log in, request an
edit token, or save the page. `mwsync.py push --preview ARTICLE` renders the
pending commit first, asks for approval, and then performs the normal
authenticated push if approved.

## Design Goal

One review-and-save workflow is:

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
fast-forwarded; if not, the preview proposal is left intact. For a pending
commit, that means `commit.mw` remains available for `push`; for an uncommitted
working-file preview, that means the working file and refs remain unchanged.
This makes `preview` the normal reminder to clean up after an on-wiki save.

If no pending commit exists, `preview` falls back to the working `.mw` file and
treats that text as an implicit preview proposal. If the user saves from the
browser and reconciliation recognizes the saved revision, mwsync should create
the corresponding local committed state from the remote revision instead of
requiring a prior local `mwsync.py commit`.

## Skipping The Local Commit Step

The shorter workflow should also be valid:

```bash
mwsync.py preview Maine
```

In this mode, `preview` renders the current working file, opens the live editor
path, and blocks in the terminal exactly as it does for a pending commit. If the
user saves on-wiki, reconciliation should fetch the saved revision and adopt it
as the local committed state.

This is intentionally not the same as creating a local `commit.mw` before
opening the browser. The user may change the text and edit summary in the live
editor. After a recognized browser save, the remote revision is authoritative:

- cache the saved revision body and metadata;
- advance `refs/upstream`, `refs/base`, and `refs/last-pushed` together;
- update `mwsync.yaml` `upstream_*` and `last_pushed_*` fields;
- rewrite the working file to the canonical saved wikitext;
- record the edit summary from the remote revision, not a guessed local
  summary.

The reconciliation output should always print the remote change message from
the saved revision. This is true even when the saved text exactly matches the
preview proposal, because the browser-authored edit summary is part of the
authoritative remote state.

The practical result is git-like: the browser save becomes the committed local
state, using the actual revision id, timestamp, editor, SHA-1, and comment that
the wiki recorded. There may be no persistent `commit.mw` artifact in this path;
the local "commit" is represented by the advanced refs, cached revision, and
updated `mwsync.yaml`.

Recognition is still verified, not assumed. For an existing page, the saved
revision must descend from the current `refs/base`. For a new page, the page
must have been absent before preview and then created with `parentid == 0`.
If no matching save is found, leave the working file and refs unchanged.

## Parser Fidelity

`preview` should call `action=parse` with `pst=1`. MediaWiki applies the
pre-save transform when saving, so a faithful preview should show the post-PST
rendered result. This affects signatures, `{{subst:...}}`, and save-time
normalization. For ordinary article edits, `pst=1` usually changes nothing.

This still has a fidelity ceiling. Unsaved content does not yet have a real
revision id, timestamp, revision user, or final saved-page context. Previewing
the proposal with PST closes most of the gap, but it is not byte-identical to
rendering the saved revision after the edit lands.

## Preview Page

The page is laid out for the common task: copy the local wikitext, open the live
editor, paste, and eyeball the rendered result. The actionable controls come
first, so the default `mwsync.py preview` needs no scrolling and no extra
options to be useful.

The page should be ordered:

1. An action panel at the top containing, in this order:
   - a **Copy wikitext** button that copies the full source field in one click,
     placed first as a reminder to copy before opening the editor;
   - an "Open source editor" link to the live wiki edit form, opened in a new
     tab (`target="_blank"` with `rel="noopener noreferrer"`) so the preview tab
     stays put and the new tab cannot reach back into the preview page;
   - the edit summary from the pending commit, when available;
   - no prefilled summary when previewing an uncommitted working file, unless a
     future option explicitly supplies one;
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

### Two Governing Principles

**1. The remote copy reigns supreme.** Once a revision exists on the wiki, that
revision — not the pending commit, not the working file — is the source of
truth. The pending commit (`commit.mw`) and the uncommitted working file are both
preview proposals. The moment the user saves on-wiki, possibly after editing the
text or the edit summary in the browser, the proposal has been superseded by the
saved revision. Reconciliation must converge local state onto that saved
revision, not defend the now-stale proposal against it.

**2. Reconciliation must never leave the local copy unusable.** This is an
invariant on *every* exit path of `preview` and `push`, including the paths that
decline to submit an edit:

> When the command returns, the working `.mw` file must be present and must
> correspond to a known base revision, and `refs/upstream`, `refs/base`,
> `refs/last-pushed`, any pending commit, and the `mwsync.yaml` push metadata
> must be mutually consistent. The command may legitimately decline to *submit*
> an edit. It may not decline to leave a *coherent* state.

The previous spec violated this invariant: it fast-forwarded only on an exact
text match and otherwise did nothing. "Do nothing" is unsafe, because a save
*did* happen — so `refs/upstream` can advance (via the auto-fetch or a later
`mwsync.py fetch`) while `refs/base`, the pending commit, and `mwsync.yaml` stay
frozen at "never pushed." The result is a half-reconciled cache: the page exists
upstream, a `create_new` commit still points at it as if it did not, and the
working file still carries text (for example, redlinks the user deleted in the
browser) that no longer matches the wiki. That is the unusable state this
section exists to prevent.

### Recognizing The User's Own Save (Lineage, Not Byte-Equality)

Byte-equality of the upstream text against the preview proposal is the wrong
test to *gate* reconciliation, because the user is expected to edit during
preview. Recognition should be based on **lineage**, which answers "is the
latest upstream revision a save the user just made from the revision they
reviewed?":

- For an edit to an existing page: the latest upstream revision's `parentid`
  equals the proposal's base revision. For a pending commit, that is
  `commit.json` `base_revid`; for an uncommitted working-file preview, that is
  `refs/base`.
- For a new page (`create_new`, `base_revid == 0`): the page did not exist when
  the commit was staged and now does, and its creating revision has
  `parentid == 0`.

When lineage holds, the saved revision is the user's own work — whether or not
its text or summary matches the staged proposal — and **remote reigns supreme**:
mwsync adopts it wholesale. Byte-equality is then used only to *classify* the
outcome for reporting, not to decide whether to converge.

### Reconciliation Outcomes

Reconciliation should always terminate in exactly one of three states, and all
three satisfy the never-unusable invariant.

1. **Clean fast-forward** — lineage holds and the upstream text matches the
   preview proposal after the narrow normalization MediaWiki applies on save
   (trailing-whitespace and final-newline). The save is the proposal, unchanged.
   Cache the canonical saved revision, advance `refs/upstream`, `refs/base`, and
   `refs/last-pushed` together, clear the pending commit if one exists, update
   `mwsync.yaml` push metadata, and report that no edit was submitted.

2. **Adopt-upstream (the divergent save)** — lineage holds but the upstream text
   or summary differs from the proposal, because the user edited in the browser
   (delinked redlinks, reworded prose, changed the edit summary, accepted a
   `ledecopy` category rewrite, etc.). This is the case that used to strand the
   user. Under "remote reigns supreme," mwsync adopts the saved revision exactly
   as the clean fast-forward does — cache it, advance all three refs together,
   update `mwsync.yaml`, clear the pending commit if one exists — and
   additionally **rewrites the working `.mw` file to the canonical saved text**
   so the local copy is immediately usable and matches the wiki. It should
   record the remote edit summary from the saved revision, report that it adopted
   an on-wiki revision that differed from the preview proposal, and ideally show
   a short diff so the divergence is not silent.

   A `create_new` commit is retired here, not preserved: the page now exists, so
   leaving `create_new: true` staged would make the next `push` attempt to create
   an existing title. Retiring the stale `create_new` proposal is part of
   converging onto the remote.

3. **Keep-proposal / hand-off** — no descendant save is found (the save failed,
   was abandoned, or was never attempted), or the latest upstream revision does
   *not* descend from the proposal base (someone else edited the page). Here
   mwsync must not claim success and must not overwrite local work. If there was
   a pending commit, it leaves the pending commit intact for a later
   `mwsync.py push`; if there was no pending commit, it leaves the working file
   and refs unchanged. If a foreign upstream revision is detected, it should
   point the user at the normal `fetch` / `merge` reconciliation path rather
   than silently absorbing someone else's edit.

### Working-File Handling

- In **clean fast-forward**, rewrite the working file to the canonical saved text
  only if it still matches the preview proposal. If the user kept editing the
  working file after `commit` or after opening preview, leave it untouched so
  those edits remain visible as local modifications against the new base.
- In **adopt-upstream**, the working file should be brought to the canonical
  saved text, because remote is authoritative and the proposal it was based on is
  gone. If — and only if — the working file contains edits made *after* the
  preview proposal that are not part of the saved revision, treat that as a
  merge situation (preserve the local edits against the new base and route
  through `merge`) rather than discarding them. The default and common case (the
  working file still equals the preview proposal) is a straight rewrite to the
  saved text.

The distinction that protects the user: mwsync may overwrite the working file
with the *remote* version when the working file is still the superseded
proposal, but it must never discard *post-commit local edits* without routing
them through `merge`.

### Implementation Gotchas

These are not obvious from the happy-path description above. Each one was a
concrete trap when reconciling an `adopt-upstream` case by hand; an implementer
should treat them as requirements, not tips.

**Canonical form is the raw API wikitext, and `upstream_sha1` is the oracle.**
The working file and the cached `<revid>.mw` body must be written from the *raw*
`result["wikitext"]`, byte-for-byte — not from the normalization used to
*compare* text. Those are two different strings: wiki content frequently has no
trailing newline, while the narrow save-time normalization (trailing-whitespace
and final-newline) appends one. Writing the normalized form to the working file
would make it differ from its own cached body and from the recorded checksum.
The recorded `upstream_sha1` is `sha1` over the raw wikitext bytes, so the
post-condition after any fast-forward or adopt-upstream is exact and cheap to
assert:

```text
sha1(working_file_bytes) == upstream_sha1   AND   working_file == <revid>.mw
```

`fsck` should check this too. If it fails, the working file was written from the
wrong (normalized) form.

**Reconciliation is a convergence/repair operation, not a one-shot forward
transaction — so it must be idempotent.** It will routinely run against a
*partially* applied state, because the post-push auto-fetch (or a manual
`mwsync.py fetch`) may already have cached the saved revision, advanced
`refs/upstream`, and appended the `history.jsonl` entry *before* reconciliation
proper runs. (That is exactly the half-state that strands the user.)
Reconciliation must therefore derive the desired end state from the authoritative
upstream revision and bring each artifact to it, treating already-correct
artifacts as no-ops — never assuming a clean "nothing cached yet" start.

In particular, **caching the canonical revision must dedupe `history.jsonl` by
revid.** Appending a revision that is already recorded must not add a second
line. The current `_cache_fetch_transaction` skips re-staging the body/meta
*files* when they already exist but still appends a history entry
unconditionally, then concatenates onto the existing history — so re-running it
for an already-cached revid duplicates the line. Fix that dedupe before reusing
the fetch transaction on the reconcile path, or the heal itself corrupts history.

**Clear the pending commit LAST, after the metadata is durably saved.** The
pending commit (`commit.json` / `commit.mw`) is the recovery anchor: while it
exists, the whole operation is re-runnable. The durable-write order must be:

1. cache the canonical revision (idempotently, per above);
2. write `refs/base` and `refs/last-pushed`;
3. persist `mwsync.yaml` push metadata to disk;
4. *only then* delete the pending commit.

Everything in steps 1–3 must be safe to repeat. If the pending commit is cleared
before `mwsync.yaml` is flushed — as the current `_record_saved_revision` does,
calling `_clear_pending_commit` immediately before `save_config` — a failure in
between destroys the anchor *and* leaves the metadata unwritten, producing a
worse, unrecoverable half-state than the one reconciliation set out to fix.

**`create_new` lineage recognition only holds while the created revision is still
the latest.** The "page now exists with `parentid == 0`" test recognizes the
user's create only when no further edit has landed on top of it. If the page was
created and then edited again (by the user or anyone), the latest revision's
`parentid` is the create's revid, not `0`, and reconciliation should fall to the
keep-pending / hand-off case and route to `fetch` / `merge` rather than adopt.
Whenever a create *is* adopted, retire the `create_new` flag as part of
converging onto the remote: a surviving `create_new` makes the next `push`
attempt to create an already-existing title, which the wiki rejects.

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
preview proposal unchanged.
```

The key property: this prompt is reached no matter what happens in the browser —
the save succeeds, the save fails, the browser crashes after **Save changes**, or
the user never saves at all. The browser is never required to report back. On
Enter, the CLI runs verified reconciliation (fetch upstream; recognize the
user's own save by **lineage** — `parentid == base_revid` / `refs/base`, or a
now-existing page with `parentid == 0` for a new-page proposal — see "On-Wiki
Save And Reconciliation"):

- If the on-wiki save landed and matches the proposal, it fast-forwards local
  state and reports the saved revision.
- If the on-wiki save landed but the user edited the text or summary in the
  browser, remote reigns supreme: it adopts the saved revision, rewrites the
  working file to the canonical saved text, and reports the divergence — it does
  not strand the user with a stale `create_new` commit.
- If nothing descending from the proposal base is found — the save failed, was
  abandoned, or was never attempted — the proposal is left intact in a coherent
  state. A pending commit can still be pushed later; an uncommitted working file
  remains a normal local modification.

Because reconciliation verifies lineage rather than assuming, every browser
outcome is safe: the prompt cannot clear or overwrite a proposal for a save that
did not land, cannot advance refs to a revision that does not exist, and —
equally important — cannot exit leaving the working file or refs in an
inconsistent, unusable state.

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
