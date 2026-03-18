#!/usr/bin/env python3
"""
mwsync.py — Per-article local ↔ MediaWiki sync tool.

Subcommands:
  add       Register a new article by URL
  fetch     Pull current wikitext from wiki to local .mw file
  push      Submit local edits back to the wiki
  diff      Compare server snapshot vs working local file
  difftool  Launch meld to compare server snapshot vs working local
  status    Show sync state of tracked articles

Usage:
  mwsync.py add https://electowiki.org/wiki/Maine
  mwsync.py fetch Maine
  mwsync.py diff Maine
  mwsync.py push Maine -m "Update Maine article"
  mwsync.py status

Credentials (for push):
  MWSYNC_MW_USER      MediaWiki bot username
  MWSYNC_MW_PASSWORD  MediaWiki bot password
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.cookiejar
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

try:
    import yaml
except ImportError:
    yaml = None

DEFAULT_CONFIG_PATH = "mwsync.yaml"
DEFAULT_API_BASE = "https://electowiki.org/w/api.php"
USER_AGENT = "mwsync/1.0 (+https://electowiki.org/)"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    if yaml is None:
        print("Error: pyyaml is not installed. Run: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(path):
        print(f"Error: config file not found: {path}", file=sys.stderr)
        print("Create a mwsync.yaml or run mwsync.py from a directory that has one.", file=sys.stderr)
        sys.exit(1)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        sys.exit(1)


def save_config(config: dict, path: str = DEFAULT_CONFIG_PATH) -> bool:
    try:
        dir_path = os.path.dirname(os.path.abspath(path))
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".yaml.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
            os.replace(tmp_path, path)
            return True
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(f"Error writing {path}: {e}", file=sys.stderr)
        return False


def resolve_article(config: dict, key: str) -> dict:
    """Look up article entry by key; exit with clear error if not found."""
    articles = config.get("wiki", {}).get("articles", {})
    if key not in articles:
        known = list(articles.keys())
        print(f"Error: article '{key}' not found in mwsync.yaml.", file=sys.stderr)
        if known:
            print(f"Known articles: {', '.join(known)}", file=sys.stderr)
        else:
            print("No articles registered yet. Use 'mwsync.py add URL' to add one.",
                  file=sys.stderr)
        sys.exit(1)
    return articles[key]


def get_api_base(config: dict) -> str:
    return config.get("wiki", {}).get("api_base", DEFAULT_API_BASE)


# ---------------------------------------------------------------------------
# MediaWiki API
# ---------------------------------------------------------------------------

def _fetch_page(title: str, api_base: str = DEFAULT_API_BASE) -> dict:
    """Fetch page wikitext and revision metadata from MediaWiki API.

    Returns dict with keys: wikitext, revid, timestamp, user, comment, sha1
    """
    params = {
        "action": "query",
        "format": "json",
        "prop": "revisions",
        "rvprop": "content|ids|timestamp|user|comment|sha1",
        "titles": title,
        "formatversion": "2",
    }
    url = api_base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    pages = data.get("query", {}).get("pages", [])
    if not pages:
        raise ValueError(f"No pages found for title '{title}'")

    page = pages[0]
    if page.get("missing"):
        raise ValueError(f"Page '{title}' does not exist on wiki")

    revs = page.get("revisions", [])
    if not revs:
        raise ValueError(f"No revisions found for '{title}'")

    rev = revs[0]
    return {
        "wikitext": rev.get("content", ""),
        "revid": rev.get("revid", 0),
        "timestamp": rev.get("timestamp", ""),
        "user": rev.get("user", ""),
        "comment": rev.get("comment", ""),
        "sha1": rev.get("sha1", ""),
    }


def _atomic_write(path: str, content: str) -> bool:
    """Atomically write text content to path. Returns True on success."""
    dir_path = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_path, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".mw.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
            return True
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(f"Error writing {path}: {e}", file=sys.stderr)
        return False


def _mw_login(api_base: str, username: str, password: str) -> urllib.request.OpenerDirector:
    """Log in to MediaWiki using bot password; return authenticated opener."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # Step 1: get login token
    params = urllib.parse.urlencode({
        "action": "query", "meta": "tokens", "type": "login", "format": "json",
    })
    req = urllib.request.Request(f"{api_base}?{params}", headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    login_token = data.get("query", {}).get("tokens", {}).get("logintoken")
    if not login_token:
        raise ValueError("Failed to get login token from MediaWiki API")

    # Step 2: POST login
    login_data = urllib.parse.urlencode({
        "action": "login",
        "lgname": username,
        "lgpassword": password,
        "lgtoken": login_token,
        "format": "json",
    }).encode("utf-8")
    req = urllib.request.Request(api_base, data=login_data,
                                 headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    login_result = result.get("login", {}).get("result")
    if login_result != "Success":
        reason = result.get("login", {}).get("reason", login_result or "unknown error")
        raise ValueError(f"MediaWiki login failed: {reason}")

    return opener


def _mw_get_csrf_token(api_base: str, opener: urllib.request.OpenerDirector) -> str:
    """Get CSRF edit token using authenticated opener."""
    params = urllib.parse.urlencode({
        "action": "query", "meta": "tokens", "type": "csrf", "format": "json",
    })
    req = urllib.request.Request(f"{api_base}?{params}", headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    token = data.get("query", {}).get("tokens", {}).get("csrftoken")
    if not token:
        raise ValueError("Failed to get CSRF token from MediaWiki API")
    return token


def _mw_edit_page(api_base: str, opener: urllib.request.OpenerDirector,
                  title: str, text: str, baserevid: int,
                  csrf_token: str, summary: str,
                  create_new: bool = False) -> int:
    """Submit a page edit to MediaWiki. Returns new revid on success."""
    params = {
        "action": "edit",
        "title": title,
        "text": text,
        "token": csrf_token,
        "summary": summary,
        "format": "json",
    }
    if create_new:
        params["createonly"] = "1"
    else:
        params["baserevid"] = str(baserevid)
    edit_data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(api_base, data=edit_data,
                                 headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if "error" in data:
        code = data["error"].get("code", "unknown")
        info = data["error"].get("info", "unknown error")
        if code == "editconflict":
            raise ValueError(
                f"Edit conflict: page was edited since revid {baserevid}. "
                f"Run 'mwsync.py fetch ARTICLE' to get the latest version, then retry."
            )
        raise ValueError(f"MediaWiki edit failed ({code}): {info}")

    edit_result = data.get("edit", {})
    if edit_result.get("result") != "Success":
        raise ValueError(f"Unexpected edit result: {edit_result.get('result', 'unknown')}")

    return edit_result.get("newrevid", 0)


def _edit_summary(default: str, key: str, title: str, baserevid: int) -> str | None:
    """Open $VISUAL/$EDITOR for edit summary. Returns summary string or None to abort."""
    comment_block = (
        f"\n# Edit summary for push to wiki.\n"
        f"# Lines starting with '#' are stripped.\n"
        f"# An empty summary aborts the push.\n"
        f"#\n"
        f"# Article: {key}\n"
        f"# Page:    {title}\n"
        f"# Base revid: {baserevid}\n"
    )
    fd, tmp_path = tempfile.mkstemp(suffix=".mwsync-summary.txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(default + comment_block)
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        subprocess.run([editor, tmp_path])
        with open(tmp_path, "r", encoding="utf-8") as f:
            raw = f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    lines = [line for line in raw.splitlines() if not line.startswith("#")]
    summary = "\n".join(lines).strip()
    return summary if summary else None


def _server_snapshot_path(key: str) -> str:
    return os.path.join("_cache", f"server--{key}.mw")


def _git_is_modified(path: str) -> bool | None:
    """Return True if file has uncommitted changes, False if clean, None if not in git."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "--", path],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if res.returncode != 0:
            return None
        return bool(res.stdout.strip())
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Subcommand runners
# ---------------------------------------------------------------------------

def run_add(args, config: dict, config_path: str) -> None:
    url = args.url
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        print(f"Error: invalid URL: {url}", file=sys.stderr)
        sys.exit(1)
    if "/wiki/" not in parsed.path:
        print(f"Error: URL does not look like a /wiki/ page: {url}", file=sys.stderr)
        sys.exit(1)

    # Derive title and key from URL path
    title_encoded = parsed.path.split("/wiki/", 1)[1]
    title = urllib.parse.unquote(title_encoded).replace("_", " ")
    key = urllib.parse.unquote(title_encoded).replace(" ", "_")
    local = key + ".mw"

    wiki = config.setdefault("wiki", {})
    articles = wiki.setdefault("articles", {})

    if key in articles:
        print(f"Error: article '{key}' is already registered in {config_path}.", file=sys.stderr)
        sys.exit(1)

    articles[key] = {
        "title": title,
        "url": url,
        "local": local,
    }

    if save_config(config, config_path):
        print(f"Registered '{key}'", file=sys.stderr)
        print(f"  title: {title}", file=sys.stderr)
        print(f"  local: {local}", file=sys.stderr)
        print(f"Run: mwsync.py fetch {key}", file=sys.stderr)


def run_fetch(args, config: dict, config_path: str) -> None:
    key = args.article
    art = resolve_article(config, key)
    title = art.get("title", key)
    local = art.get("local", key + ".mw")
    api_base = get_api_base(config)
    dry_run = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)

    if dry_run:
        print(f"# Fetch plan for: {key}", file=sys.stderr)
        print(f"#   Title:    {title}", file=sys.stderr)
        print(f"#   API:      {api_base}", file=sys.stderr)
        print(f"#   Local:    {local}", file=sys.stderr)
        print(f"#   Snapshot: {_server_snapshot_path(key)}", file=sys.stderr)
        prev = art.get("upstream_revid")
        if prev:
            print(f"#   Current upstream_revid: {prev}", file=sys.stderr)
        return

    # Refuse to overwrite uncommitted local changes
    if os.path.exists(local) and not force:
        modified = _git_is_modified(local)
        if modified:
            print(f"Error: '{local}' has uncommitted changes.", file=sys.stderr)
            print("Commit or stash your changes, or use --force to overwrite.",
                  file=sys.stderr)
            sys.exit(1)

    print(f"# Fetching '{title}' from {api_base}...", file=sys.stderr)
    try:
        result = _fetch_page(title, api_base)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    revid = result["revid"]
    wikitext = result["wikitext"]
    print(f"# Got revid {revid} ({len(wikitext)} chars)", file=sys.stderr)

    if not _atomic_write(local, wikitext):
        sys.exit(1)
    print(f"# Wrote {local}", file=sys.stderr)

    snapshot = _server_snapshot_path(key)
    if not _atomic_write(snapshot, wikitext):
        sys.exit(1)
    print(f"# Wrote server snapshot {snapshot}", file=sys.stderr)

    # Update config metadata
    wiki = config.setdefault("wiki", {})
    articles = wiki.setdefault("articles", {})
    art = articles.setdefault(key, {})
    art["upstream_revid"] = revid
    art["upstream_timestamp"] = result["timestamp"]
    art["upstream_editor"] = result["user"]
    art["upstream_summary"] = result["comment"]
    art["upstream_sha1"] = result["sha1"]
    save_config(config, config_path)
    print(f"# Updated upstream_revid={revid} in {config_path}", file=sys.stderr)

    # Suggested commit message
    comment = result["comment"].replace("\n", " ").strip()
    print("\nSuggested commit message:", file=sys.stderr)
    print(f"  Synced '{key}' with revid {revid}", file=sys.stderr)
    print(f"  {art.get('url', '')}", file=sys.stderr)
    if comment:
        print(f"  wiki comment: '{comment}'", file=sys.stderr)


def run_push(args, config: dict, config_path: str) -> None:
    key = args.article
    art = resolve_article(config, key)
    title = art.get("title", key)
    local = art.get("local", key + ".mw")
    api_base = get_api_base(config)
    baserevid = art.get("upstream_revid", 0)
    dry_run = getattr(args, "dry_run", False)
    message = getattr(args, "message", None)
    create_new = getattr(args, "new", False)

    if not baserevid and not create_new:
        print(f"Error: upstream_revid not set for '{key}'.", file=sys.stderr)
        print(f"If this is a new article, use 'mwsync.py push --new {key}' to create it.",
              file=sys.stderr)
        print(f"Otherwise, run 'mwsync.py fetch {key}' first.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(local):
        print(f"Error: local file not found: {local}", file=sys.stderr)
        print(f"Run 'mwsync.py fetch {key}' first.", file=sys.stderr)
        sys.exit(1)

    username = os.environ.get("MWSYNC_MW_USER", "")
    password = os.environ.get("MWSYNC_MW_PASSWORD", "")

    if dry_run:
        try:
            page_len = len(open(local, encoding="utf-8").read())
        except Exception:
            page_len = 0
        print(f"# Push plan for: {key}", file=sys.stderr)
        print(f"#   Title:      {title}", file=sys.stderr)
        print(f"#   API:        {api_base}", file=sys.stderr)
        print(f"#   Local:      {local} ({page_len} chars)", file=sys.stderr)
        if create_new:
            print("#   Mode:       CREATE NEW article", file=sys.stderr)
        else:
            print(f"#   Base revid: {baserevid}", file=sys.stderr)
        if message:
            print(f"#   Summary:    {message}", file=sys.stderr)
        else:
            print("#   Summary:    (editor will open)", file=sys.stderr)
        if username:
            print(f"#   Credentials: found (user: {username})", file=sys.stderr)
        else:
            print("#   Credentials: not set (MWSYNC_MW_USER / MWSYNC_MW_PASSWORD)",
                  file=sys.stderr)
        return

    if not username or not password:
        print("Error: push requires credentials.", file=sys.stderr)
        print("Set MWSYNC_MW_USER and MWSYNC_MW_PASSWORD environment variables.",
              file=sys.stderr)
        sys.exit(1)

    try:
        with open(local, "r", encoding="utf-8") as f:
            page_text = f.read()
    except Exception as e:
        print(f"Error reading {local}: {e}", file=sys.stderr)
        sys.exit(1)

    if message:
        summary = message
    else:
        summary = _edit_summary("", key, title, baserevid)
        if summary is None:
            print("# Aborted: empty edit summary.", file=sys.stderr)
            sys.exit(0)

    print(f"# Pushing '{key}'...", file=sys.stderr)
    print(f"#   Title:      {title}", file=sys.stderr)
    print(f"#   Content:    {len(page_text)} chars", file=sys.stderr)
    if create_new:
        print("#   Mode:       CREATE NEW article", file=sys.stderr)
    else:
        print(f"#   Base revid: {baserevid}", file=sys.stderr)
    print(f"#   Summary:    {summary}", file=sys.stderr)

    print(f"# Logging in as {username}...", file=sys.stderr)
    try:
        opener = _mw_login(api_base, username, password)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("# Getting CSRF token...", file=sys.stderr)
    try:
        csrf_token = _mw_get_csrf_token(api_base, opener)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("# Submitting edit...", file=sys.stderr)
    try:
        new_revid = _mw_edit_page(api_base, opener, title, page_text,
                                   baserevid, csrf_token, summary,
                                   create_new=create_new)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"# Success! New revid: {new_revid}", file=sys.stderr)

    now_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wiki = config.setdefault("wiki", {})
    articles = wiki.setdefault("articles", {})
    art = articles.setdefault(key, {})
    art["last_pushed_revid"] = new_revid
    art["last_pushed_at"] = now_utc
    save_config(config, config_path)

    # Auto-fetch to resync upstream_revid with the revision we just created
    print("# Re-fetching to sync server snapshot...", file=sys.stderr)
    try:
        result = _fetch_page(title, api_base)
        _atomic_write(local, result["wikitext"])
        _atomic_write(_server_snapshot_path(key), result["wikitext"])
        art["upstream_revid"] = result["revid"]
        art["upstream_timestamp"] = result["timestamp"]
        art["upstream_editor"] = result["user"]
        art["upstream_summary"] = result["comment"]
        art["upstream_sha1"] = result["sha1"]
        save_config(config, config_path)
        print(f"# Synced upstream_revid={result['revid']}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: auto-fetch failed: {e}", file=sys.stderr)


def run_diff(args, config: dict, config_path: str) -> None:
    key = args.article
    art = resolve_article(config, key)
    local = art.get("local", key + ".mw")
    snapshot = _server_snapshot_path(key)

    if getattr(args, "remote", False):
        title = art.get("title", key)
        api_base = get_api_base(config)
        print(f"# Re-fetching server snapshot for '{key}'...", file=sys.stderr)
        try:
            result = _fetch_page(title, api_base)
            _atomic_write(snapshot, result["wikitext"])
            print(f"# Got revid {result['revid']}", file=sys.stderr)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    if not os.path.exists(snapshot):
        print(f"Error: no server snapshot found at {snapshot}.", file=sys.stderr)
        print(f"Run 'mwsync.py fetch {key}' first.", file=sys.stderr)
        sys.exit(1)

    subprocess.run(["git", "diff", "--no-index", snapshot, local])


def run_difftool(args, config: dict, config_path: str) -> None:
    key = args.article
    art = resolve_article(config, key)
    local = art.get("local", key + ".mw")
    snapshot = _server_snapshot_path(key)

    if not os.path.exists(snapshot):
        print(f"Error: no server snapshot found at {snapshot}.", file=sys.stderr)
        print(f"Run 'mwsync.py fetch {key}' first.", file=sys.stderr)
        sys.exit(1)

    subprocess.run(["meld", snapshot, local])


def run_status(args, config: dict, config_path: str) -> None:
    articles = config.get("wiki", {}).get("articles", {})
    if not articles:
        print("No articles registered. Use 'mwsync.py add URL' to add one.")
        return

    key_filter = getattr(args, "article", None)
    if key_filter:
        if key_filter not in articles:
            print(f"Error: article '{key_filter}' not found.", file=sys.stderr)
            sys.exit(1)
        items = [(key_filter, articles[key_filter])]
    else:
        items = list(articles.items())

    for key, art in items:
        local = art.get("local", key + ".mw")
        revid = art.get("upstream_revid", "")
        ts = art.get("upstream_timestamp", "")
        editor = art.get("upstream_editor", "")
        pushed_revid = art.get("last_pushed_revid", "")
        pushed_at = art.get("last_pushed_at", "")

        modified = _git_is_modified(local)
        if modified is True:
            flag = "[modified]"
        elif modified is False:
            flag = "[clean]"
        else:
            flag = ""

        print(key)
        print(f"  local:           {local}  {flag}".rstrip())
        if revid:
            rev_info = str(revid)
            if ts:
                rev_info += f"  ({ts}"
                if editor:
                    rev_info += f" by {editor}"
                rev_info += ")"
            print(f"  upstream_revid:  {rev_info}")
        else:
            print("  upstream_revid:  (not fetched)")
        if pushed_revid:
            print(f"  last_pushed:     {pushed_revid}  ({pushed_at})")
        else:
            print("  last_pushed:     (never)")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="mwsync.py",
        description="Sync individual MediaWiki articles to and from local .mw files.",
        epilog=(
            "Credentials for push: set MWSYNC_MW_USER and MWSYNC_MW_PASSWORD "
            "environment variables."
        ),
    )
    ap.add_argument(
        "--config", default=DEFAULT_CONFIG_PATH,
        help=f"Path to config file (default: {DEFAULT_CONFIG_PATH})",
    )
    sub = ap.add_subparsers(dest="subcommand", help="Available subcommands")

    # add
    p_add = sub.add_parser("add", help="Register a new article by URL")
    p_add.add_argument("url", metavar="URL", help="Full wiki page URL")

    # fetch
    p_fetch = sub.add_parser("fetch", help="Pull current wikitext from wiki to local .mw file")
    p_fetch.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")
    p_fetch.add_argument("--dry-run", action="store_true", help="Preview without writing")
    p_fetch.add_argument("--force", action="store_true",
                         help="Overwrite local file even if uncommitted changes exist")

    # push
    p_push = sub.add_parser("push", help="Submit local edits back to the wiki")
    p_push.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")
    p_push.add_argument("--dry-run", action="store_true", help="Preview without pushing")
    p_push.add_argument("--new", action="store_true",
                        help="Create a new article (instead of editing an existing one)")
    p_push.add_argument("-m", "--message", help="Edit summary (skips editor prompt)")

    # diff
    p_diff = sub.add_parser("diff", help="Compare server snapshot vs working local file")
    p_diff.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")
    p_diff.add_argument("--remote", action="store_true",
                        help="Re-fetch server snapshot before diffing")

    # difftool
    p_difftool = sub.add_parser("difftool",
                                help="Launch meld to compare server snapshot vs local")
    p_difftool.add_argument("article", metavar="ARTICLE", help="Article key (from mwsync.yaml)")

    # status
    p_status = sub.add_parser("status", help="Show sync state of tracked articles")
    p_status.add_argument("article", metavar="ARTICLE", nargs="?",
                          help="Article key (omit to show all)")

    args = ap.parse_args()

    if not args.subcommand:
        ap.print_help()
        sys.exit(0)

    config_path = args.config
    config = load_config(config_path)

    if args.subcommand == "add":
        run_add(args, config, config_path)
    elif args.subcommand == "fetch":
        run_fetch(args, config, config_path)
    elif args.subcommand == "push":
        run_push(args, config, config_path)
    elif args.subcommand == "diff":
        run_diff(args, config, config_path)
    elif args.subcommand == "difftool":
        run_difftool(args, config, config_path)
    elif args.subcommand == "status":
        run_status(args, config, config_path)


if __name__ == "__main__":
    main()
