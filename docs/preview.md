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

To address the security limitations of opening local `file://` URIs directly in a browser (where scripts could run with elevated privileges or trigger cross-origin restrictions), `mwsync` can host the generated preview using a lightweight, short-lived HTTP server. 

Instead of introducing third-party dependencies like Flask, this server can be implemented directly using Python's standard `http.server` and `socketserver` libraries:

1. **Transient Lifetime:** The server runs in a background thread and automatically triggers `server.shutdown()` immediately after serving the preview HTML page once.
2. **Loopback-Only Binding:** The server binds strictly to `127.0.0.1` (localhost) rather than `0.0.0.0`, preventing exposure to the local network.
3. **Randomized Port:** It binds to port `0`, allowing the operating system to allocate an ephemeral, conflict-free port.
4. **Single-Use Access Token:** The script generates a cryptographically secure random token (using Python's built-in `secrets` library) and opens the browser with the token in the URL query string (e.g., `http://127.0.0.1:PORT/?token=XYZ`). The server rejects any incoming request that does not match this token, preventing other local users or processes on the host machine from snooping on unpublished draft content.

