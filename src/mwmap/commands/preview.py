"""Implementation of `mwmap preview`.

Renders the staged commit (preferred) or working file through the remote
MediaWiki parser, writes `preview.html`, and optionally opens a transient
loopback browser preview. Interactive mode can reconcile a manual browser save
back into mwmap state, mirroring legacy `mwsync.py preview`.

Typical call stack:
  run_preview()
    -> _select_mapping()
    -> _preview_source()
    -> remote.preview_wikitext()
    -> _preview_document()
    -> _reconcile_saved_preview()  # interactive mode only
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import html
import http.server
from pathlib import Path
import re
import secrets
import socketserver
import sys
import threading
from typing import Any
from urllib import parse
import webbrowser

from mwmap.core.mediawiki import MediaWikiError
from mwmap.core.misc import atomic_write_text, die
from mwmap.sync import remote_for_mapping, write_local_body
from mwmap.workspace import (
    cache_page_rev,
    clear_pending_commit,
    iter_page_mappings,
    load_workspace_config,
    pending_commit_body_path,
    preview_html_path,
    read_pending_commit,
    save_workspace_config,
    update_cache_base,
)


def run_preview(args: argparse.Namespace) -> int:
    """Render one mapped page through its remote parser."""
    config = load_workspace_config(args.root)
    mapping = _select_mapping(args.root, config, getattr(args, "path", None))
    remote_def = (config.get("remotes") or {}).get(mapping["remote"], {})
    remote = remote_for_mapping(config, mapping, {})
    source = _preview_source(args.root, mapping, remote_def.get("location", ""))

    try:
        rendered = remote.preview_wikitext(source["title"], source["text"])
    except MediaWikiError as exc:
        die(f"rendering preview failed for {source['label']}: {exc}")

    output = _output_path(args.root, mapping, getattr(args, "output", None))
    document = _preview_document(remote_def.get("location", ""), mapping, rendered, source)
    atomic_write_text(output, document)

    print(f"Rendered preview for {source['path']}")
    print(f"  page: {source['page_url']}")
    print(f"  html: {output}")

    if _file_mode(args):
        file_url = output.resolve().as_uri()
        print(f"  link: {file_url}")
        if getattr(args, "open", False) and webbrowser.open(file_url):
            print(f"Opened {file_url}")
        return 0

    _preview_url, server = _open_transient_preview(document)
    try:
        input(
            "After saving in the browser, press Enter to reconcile "
            "(Ctrl-C leaves preview proposal unchanged)."
        )
    except (KeyboardInterrupt, EOFError):
        print("\nPreview proposal left unchanged.", file=sys.stderr)
        return 0
    finally:
        _close_preview_server(server)

    return _reconcile_saved_preview(args.root, config, mapping, remote, source)


def _select_mapping(root: Path, config: dict[str, Any], path: str | None) -> dict[str, Any]:
    """Choose exactly one page mapping, preferring a sole pending commit."""
    mappings = iter_page_mappings(config, path)
    if not mappings:
        target = f" for {path}" if path else ""
        die(f"no page mapping{target} to preview")
    if path is not None:
        return mappings[0]

    pending = [
        mapping
        for mapping in mappings
        if read_pending_commit(root, mapping["remote"], mapping["pageid"]) is not None
    ]
    if len(pending) == 1:
        return pending[0]
    if len(pending) > 1:
        die("multiple pending commits; pass PATH to preview one")
    if len(mappings) == 1:
        return mappings[0]
    die("multiple page mappings; pass PATH to preview one")


def _preview_source(root: Path, mapping: dict[str, Any], location: str) -> dict[str, Any]:
    """Return source text and metadata for a pending commit or working file."""
    if mapping.get("format", "mw") != "mw":
        die(f"preview currently supports only raw MediaWiki format: {mapping.get('format')!r}")

    label = f"{mapping.get('remote')}:{mapping.get('remote_path')}"
    page_url = _view_url(location, str(mapping.get("remote_path") or ""))
    pending = read_pending_commit(root, mapping["remote"], mapping["pageid"])
    if pending is not None:
        body_path = pending_commit_body_path(root, mapping["remote"], mapping["pageid"])
        return {
            "kind": "pending commit",
            "text": body_path.read_text(encoding="utf-8"),
            "path": body_path,
            "pending": pending,
            "title": pending.get("title") or mapping.get("remote_path"),
            "summary": pending.get("summary") or "",
            "base_revid": pending.get("base_revid"),
            "label": label,
            "page_url": page_url,
        }

    local_path = root / mapping["local_path"]
    if not local_path.exists():
        die(f"local file not found: {local_path}. Run: mwmap merge {mapping['local_path']}")
    return {
        "kind": "working file",
        "text": local_path.read_text(encoding="utf-8"),
        "path": local_path,
        "pending": None,
        "title": mapping.get("remote_path"),
        "summary": "",
        "base_revid": mapping.get("base_revid"),
        "label": label,
        "page_url": page_url,
    }


def _output_path(root: Path, mapping: dict[str, Any], output: str | None) -> Path:
    """Return explicit output path or the disposable cache preview path."""
    if output:
        path = Path(output)
        return path if path.is_absolute() else root / path
    return preview_html_path(root, mapping["remote"], mapping["pageid"])


def _file_mode(args: argparse.Namespace) -> bool:
    """Return whether preview should avoid the transient interactive server."""
    return bool(getattr(args, "output", None) or getattr(args, "link", False) or not sys.stdin.isatty())


def _site_root(location: str) -> str:
    """Return the scheme/host root for a remote location."""
    parsed = parse.urlparse(location)
    return parse.urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def _index_url(location: str, title: str, summary: str = "") -> str:
    """Return an edit URL derived from the remote location."""
    parsed = parse.urlparse(location)
    path = parsed.path.rstrip("/")
    if path.endswith("/api.php"):
        index_path = path[: -len("/api.php")] + "/index.php"
    elif path.endswith("/w"):
        index_path = path + "/index.php"
    else:
        index_path = "/w/index.php"
    params = {"title": title, "action": "edit"}
    if summary:
        params["summary"] = summary[:500]
    return parse.urlunparse(
        (parsed.scheme, parsed.netloc, index_path, "", parse.urlencode(params), "")
    )


def _view_url(location: str, title: str) -> str:
    """Return a view URL derived from the remote location."""
    parsed = parse.urlparse(location)
    path = parsed.path.rstrip("/")
    if path.endswith("/api.php"):
        index_path = path[: -len("/api.php")] + "/index.php"
    elif path.endswith("/w"):
        index_path = path + "/index.php"
    else:
        index_path = "/w/index.php"
    return parse.urlunparse(
        (parsed.scheme, parsed.netloc, index_path, "", parse.urlencode({"title": title}), "")
    )


def _absolutize_preview_urls(rendered_html: str, site_root: str) -> str:
    """Make relative preview links usable from a local preview document."""
    root = site_root.rstrip("/")

    def repl(match: re.Match[str]) -> str:
        attr, quote, url = match.group(1), match.group(2), match.group(3)
        parsed = parse.urlparse(url)
        if not url or parsed.scheme or url.startswith("#") or url.startswith("mailto:"):
            return match.group(0)
        if url.startswith("//"):
            return f"{attr}={quote}https:{url}{quote}"
        return f"{attr}={quote}{parse.urljoin(root + '/', url)}{quote}"

    return re.sub(r'\b(href|src)=(["\'])([^"\']*)\2', repl, rendered_html)


_PREVIEW_COPY_SCRIPT = (
    "var b=document.getElementById('mwmap-copy');"
    "var t=document.getElementById('mwmap-wikitext');"
    "b.addEventListener('click',function(){"
    "t.focus();t.select();"
    "var d=function(){b.textContent='Copied wikitext';"
    "setTimeout(function(){b.textContent='Copy wikitext';},1500);};"
    "if(navigator.clipboard&&navigator.clipboard.writeText){"
    "navigator.clipboard.writeText(t.value).then(d,function(){"
    "document.execCommand('copy');d();});"
    "}else{document.execCommand('copy');d();}"
    "});"
)


def _preview_csp() -> str:
    """Return a CSP that pins the one inline copy helper by sha256."""
    digest = base64.b64encode(
        hashlib.sha256(_PREVIEW_COPY_SCRIPT.encode("utf-8")).digest()
    ).decode("ascii")
    return (
        "default-src 'none'; img-src https: data:; style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'; "
        f"script-src 'sha256-{digest}'"
    )


def _preview_document(
    location: str, mapping: dict[str, Any], rendered: dict[str, Any], source: dict[str, Any]
) -> str:
    """Return the complete preview HTML document."""
    site_root = _site_root(location)
    title = str(source.get("title") or mapping.get("remote_path") or "")
    summary = str(source.get("summary") or "")
    edit_url = _index_url(location, title, summary)
    body_html = _absolutize_preview_urls(str(rendered.get("text") or ""), site_root)
    categories_html = rendered.get("categorieshtml") or ""
    if isinstance(categories_html, dict):
        categories_html = categories_html.get("*", "")
    categories_html = _absolutize_preview_urls(str(categories_html), site_root)
    display_title = html.escape(str(rendered.get("displaytitle") or title))
    generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wiki_host = parse.urlparse(location).netloc or "the wiki"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Preview: {html.escape(title)}</title>
  <style>
    body {{ margin: 0; background: #f8f9fa; color: #202122; font: 16px/1.55 sans-serif; }}
    .mwmap-preview-shell {{ max-width: 980px; margin: 0 auto; background: #fff; min-height: 100vh; padding: 1.5rem 2rem 4rem; box-shadow: 0 0 0 1px #a2a9b1; }}
    .mwmap-actions {{ border: 1px solid #a2a9b1; background: #f8f9fa; padding: 1rem 1.25rem; margin-bottom: 1.5rem; }}
    .mwmap-actions h2 {{ margin: 0 0 .5rem; font-size: 1.1rem; }}
    .mwmap-actions-bar {{ display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; margin: .5rem 0; }}
    .mwmap-edit-link {{ display: inline-block; padding: .45rem .9rem; background: #36c; color: #fff; border-radius: 2px; text-decoration: none; font-weight: bold; }}
    #mwmap-copy {{ padding: .45rem .9rem; font: inherit; cursor: pointer; border: 1px solid #a2a9b1; border-radius: 2px; background: #fff; }}
    .mwmap-summary {{ margin: .5rem 0; font-size: .95rem; }}
    .mwmap-actions textarea {{ width: 100%; min-height: 14rem; font: 13px/1.45 monospace; box-sizing: border-box; }}
    .mwmap-meta {{ margin-top: .75rem; font-size: .85rem; color: #54595d; }}
    .mwmap-meta p {{ margin: .25rem 0; }}
    .mwmap-rendered-label {{ font-size: .85rem; color: #54595d; text-transform: uppercase; letter-spacing: .05em; margin: 0 0 .5rem; }}
    h1 {{ font-family: Georgia, 'Times New Roman', serif; font-weight: normal; border-bottom: 1px solid #a2a9b1; margin-top: 0; }}
    a {{ color: #0645ad; }}
    pre, code {{ background: #f8f9fa; }}
    table {{ border-collapse: collapse; }}
    th, td {{ border: 1px solid #a2a9b1; padding: .2rem .4rem; }}
    .catlinks {{ border: 1px solid #a2a9b1; background: #f8f9fa; margin-top: 1.5rem; padding: .4rem .7rem; font-size: .9rem; }}
  </style>
</head>
<body>
  <main class="mwmap-preview-shell">
    <section class="mwmap-actions">
      <h2>Local mwmap preview - not yet saved</h2>
      <div class="mwmap-actions-bar">
        <button type="button" id="mwmap-copy">Copy wikitext</button>
        <a class="mwmap-edit-link" href="{html.escape(edit_url, quote=True)}" target="_blank" rel="noopener noreferrer">Open source editor on {html.escape(wiki_host)}</a>
      </div>
      <p class="mwmap-summary">Edit summary: <code>{html.escape(summary or "(none)")}</code></p>
      <textarea id="mwmap-wikitext" readonly>{html.escape(str(source.get("text") or ""))}</textarea>
      <details class="mwmap-meta">
        <summary>Preview details</summary>
        <p>Source: {html.escape(str(source.get("path") or ""))} ({html.escape(str(source.get("kind") or ""))})</p>
        <p>Remote: {html.escape(str(mapping.get("remote") or ""))}</p>
        <p>Pageid: {html.escape(str(mapping.get("pageid") or ""))}</p>
        <p>Title: {html.escape(title)}</p>
        <p>Local path: {html.escape(str(mapping.get("local_path") or ""))}</p>
        <p>Base revid: {html.escape(str(source.get("base_revid") or "(none)"))}</p>
        <p>Generated: {html.escape(generated_at)}</p>
      </details>
    </section>
    <p class="mwmap-rendered-label">Rendered preview (scroll for the full page)</p>
    <h1>{display_title}</h1>
    {body_html}
    {categories_html}
  </main>
  <script>{_PREVIEW_COPY_SCRIPT}</script>
</body>
</html>
"""


class _PreviewHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Tiny one-shot local HTTP server for browser preview."""

    daemon_threads = True
    allow_reuse_address = False


def _serve_preview_document(document: str, *, timeout: int = 300) -> tuple[str, object]:
    """Serve a preview document once on 127.0.0.1 behind an unguessable path."""
    token = secrets.token_urlsafe(32)
    route = f"/preview/{token}"
    payload = document.encode("utf-8")
    ready = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "mwmap-preview/0.1"

        def log_message(self, _format: str, *args: Any) -> None:
            return

        def _valid_host(self) -> bool:
            expected = f"127.0.0.1:{self.server.server_port}"
            return self.headers.get("Host", "") == expected

        def _headers(self, status: int, length: int = 0) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", _preview_csp())
            self.end_headers()

        def _reject(self, status: int) -> None:
            self._headers(status, 0)

        def _serve(self, include_body: bool) -> None:
            if self.path != route or not secrets.compare_digest(self.path, route):
                self._reject(404)
                return
            if not self._valid_host():
                self._reject(404)
                return
            self._headers(200, len(payload))
            if include_body:
                self.wfile.write(payload)
                ready.set()
                threading.Thread(target=self.server.shutdown, daemon=True).start()

        def do_GET(self) -> None:
            self._serve(True)

        def do_HEAD(self) -> None:
            self._serve(False)

        def do_POST(self) -> None:
            self._reject(405)

    server = _PreviewHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def expire() -> None:
        if not ready.wait(timeout):
            server.shutdown()

    threading.Thread(target=expire, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}{route}"
    return url, server


def _open_transient_preview(document: str) -> tuple[str, object]:
    """Serve and open the one-shot browser preview."""
    preview_url, server = _serve_preview_document(document)
    print(f"  served: {preview_url}")
    if webbrowser.open(preview_url):
        print(f"Opened {preview_url}")
    else:
        print(f"Could not open browser; open this URL manually: {preview_url}")
    return preview_url, server


def _close_preview_server(server: object) -> None:
    """Shut down the transient preview server if it is still alive."""
    try:
        server.shutdown()
        server.server_close()
    except Exception:
        pass


def _reconcile_saved_preview(
    root: Path,
    config: dict[str, Any],
    mapping: dict[str, Any],
    remote: Any,
    source: dict[str, Any],
) -> int:
    """Adopt a compatible manual browser save into cache/config state."""
    base_revid = _int_or_zero(source.get("base_revid"))
    if not base_revid:
        print("No base revision recorded; preview proposal left unchanged.")
        return 0

    content, metadata = remote.fetch_page_by_id(mapping["pageid"])
    latest_revid = _int_or_zero(metadata.get("revid"))
    parentid = _int_or_zero(metadata.get("parentid"))
    if latest_revid == base_revid:
        print("No matching on-wiki save found; preview proposal left unchanged.")
        _print_pending_still_exists(root, mapping)
        return 0
    if parentid != base_revid:
        print(
            f"Warning: latest revision parentid {parentid} != expected base {base_revid}.",
            file=sys.stderr,
        )
        print("Preview proposal left unchanged; run mwmap pull to reconcile manually.")
        return 0

    cache_page_rev(root, remote.name, content, metadata)
    update_cache_base(root, remote.name, mapping["pageid"], latest_revid)
    mapping["base_revid"] = latest_revid
    target = root / mapping["local_path"]
    if target.exists() and target.read_text(encoding="utf-8") == source["text"]:
        write_local_body(mapping.get("format", "mw"), target, content)
    clear_pending_commit(root, mapping["remote"], mapping["pageid"])
    save_workspace_config(root, config)

    if _normalized_saved_text(content) == _normalized_saved_text(source["text"]):
        print(f"Already saved upstream as rev {latest_revid}; adopted into mwmap state.")
    else:
        print(f"Adopted upstream rev {latest_revid}; saved text differs from preview proposal.")
    return 0


def _print_pending_still_exists(root: Path, mapping: dict[str, Any]) -> None:
    """Tell the user when a pending commit remains after no-op reconcile."""
    if read_pending_commit(root, mapping["remote"], mapping["pageid"]) is not None:
        print(f"Pending commit still exists: {pending_commit_body_path(root, mapping['remote'], mapping['pageid'])}")


def _normalized_saved_text(text: str) -> str:
    """Normalize text for comparing saved wiki content to preview proposals."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).rstrip("\n") + "\n"


def _int_or_zero(value: Any) -> int:
    """Best-effort integer coercion used for revision comparisons."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
