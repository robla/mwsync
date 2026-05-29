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

## Future Directions

A local web server is useful, but it is not what makes the preview meaningfully
closer to "preview before pushing." The important semantic improvement is to
preview the exact content that `push` would submit.

Near-term recommendation:

```bash
mwsync.py commit Maine -m "Update Maine"
mwsync.py preview Maine
mwsync.py push Maine
```

When `_cache/<Article_Key>/commit.mw` exists, `preview` should prefer that
pending commit snapshot over the mutable working file. That makes preview
match the push path: the user previews the exact wikitext that `mwsync.py push`
will submit. A future `--working` flag could force previewing the editable
local `.mw` file instead, but the default should support the pre-push review
workflow.

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
   Do not expose `_cache/`, the repository, or the working directory.
2. **Loopback-only binding:** Bind strictly to `127.0.0.1`, not `localhost`
   and not `0.0.0.0`.
3. **Randomized port:** Bind to port `0` so the operating system allocates an
   ephemeral, conflict-free port.
4. **Access token:** Generate a cryptographically secure token with Python's
   `secrets` module and put it in the path, such as
   `http://127.0.0.1:PORT/preview/TOKEN`.
5. **Strict response headers:** Send `Referrer-Policy: no-referrer` and a
   restrictive Content Security Policy. A reasonable starting point is
   `default-src 'none'; img-src https: data:; style-src 'unsafe-inline';
   base-uri 'none'; form-action 'none'; script-src 'none'`.
6. **Short lifetime:** Use a short timeout and shut down after serving the
   valid preview route. Do not shut down after the first request blindly,
   because browser requests for `/favicon.ico` or prefetches could consume it.
7. **No proxying:** Do not proxy arbitrary requests to Electowiki or other
   hosts through the local server.

The main risk is accidentally turning preview HTML into an active local web app
that can execute scripts or interact with other localhost services. The server
should be a narrow, inert document viewer.
