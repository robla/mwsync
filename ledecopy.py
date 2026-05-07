#!/usr/bin/env python3
"""ledecopy.py — Copy an enwiki article's lede into an Electowiki draft.

Usage:
  ledecopy.py "New York"

The argument is an enwiki page title. ledecopy fetches the page from English
Wikipedia, extracts the lede (the wikitext before the first level-2 heading),
strips obvious non-prose top-of-page templates, adds Electowiki attribution
templates, and writes the result to a local <Article_Key>.mw file plus an
entry in mwsync.yaml. The resulting draft is ready for `mwsync.py push --new`.

ledecopy refuses to run if the local file exists, the article key is already
registered in mwsync.yaml, the enwiki source is a redirect, or the target
Electowiki page already exists. There is no override flag.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

import mwsync

ENWIKI_API = "https://en.wikipedia.org/w/api.php"
ELECTOWIKI_API = "https://electowiki.org/w/api.php"
ELECTOWIKI_BASE = "https://electowiki.org"

# Top-of-page templates that ledecopy strips before splitting on the first
# level-2 heading. Match is case-insensitive and runs against the template's
# first segment (the part before the first '|'). Intentionally narrow; the
# spec calls for conservative removal.
STRIP_EXACT_NAMES = frozenset({
    "short description",
    "about", "for", "distinguish", "redirect",
    "other uses", "other people", "main", "hatnote",
    "multiple issues", "cleanup", "refimprove", "more citations needed",
    "pov", "update", "unreliable sources", "original research",
    "notability", "tone",
    "use dmy dates", "use mdy dates",
    "use british english", "use american english",
    "good article", "featured article",
})

LEVEL2_HEADING_RE = re.compile(r"^==(?!=)[^\n]*?(?<!=)==\s*$", re.MULTILINE)
CATEGORY_RE = re.compile(r"\[\[\s*[Cc]ategory\s*:[^\]\n]+\]\]")
REF_TAG_RE = re.compile(r"<ref(?:\s|>|/)", re.IGNORECASE)
REDIRECT_RE = re.compile(r"\s*#REDIRECT\s*\[\[([^\]]+)\]\]", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Source analysis
# ---------------------------------------------------------------------------

def _is_redirect(wikitext: str) -> tuple[bool, str | None]:
    match = REDIRECT_RE.match(wikitext)
    if not match:
        return False, None
    target = match.group(1).split("|", 1)[0].strip()
    return True, target or None


def _matches_strip_pattern(name: str) -> bool:
    name = name.strip().lower()
    if name in STRIP_EXACT_NAMES:
        return True
    if name == "infobox" or name.startswith("infobox "):
        return True
    if name.startswith("pp-"):
        return True
    return False


def _find_matching_brace_end(text: str, start: int) -> int:
    """Given text[start:start+2] == '{{', return index just past the matching '}}'.

    Returns -1 if braces are not balanced from the starting point.
    """
    depth = 0
    i = start
    n = len(text)
    while i < n - 1:
        if text[i] == "{" and text[i + 1] == "{":
            depth += 1
            i += 2
        elif text[i] == "}" and text[i + 1] == "}":
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return -1


def _strip_top_templates(source: str) -> str:
    cursor = 0
    n = len(source)
    while cursor < n:
        ws_start = cursor
        while cursor < n and source[cursor] in " \t\n\r":
            cursor += 1
        if cursor >= n:
            break
        if cursor + 1 < n and source[cursor] == "{" and source[cursor + 1] == "{":
            end = _find_matching_brace_end(source, cursor)
            if end < 0:
                cursor = ws_start
                break
            template_body = source[cursor + 2:end - 2]
            name = template_body.split("|", 1)[0]
            if _matches_strip_pattern(name):
                cursor = end
                continue
            cursor = ws_start
            break
        cursor = ws_start
        break
    return source[cursor:].lstrip("\n\r")


def _split_lede(source: str) -> str:
    match = LEVEL2_HEADING_RE.search(source)
    if match:
        return source[:match.start()]
    return source


def _extract_categories(source: str) -> list[str]:
    return CATEGORY_RE.findall(source)


def _has_refs(text: str) -> bool:
    return bool(REF_TAG_RE.search(text))


# ---------------------------------------------------------------------------
# Output assembly
# ---------------------------------------------------------------------------

def _build_output(title: str, lede: str, has_refs: bool, revid: int,
                  categories: list[str]) -> str:
    blocks = [f"{{{{Wikipedia|{title}}}}}"]
    blocks.append(lede.strip())
    if has_refs:
        blocks.append("== References ==\n<references/>")
    blocks.append(f"{{{{Fromwikipedia|{title}|oldid={revid}}}}}")
    if categories:
        blocks.append("\n".join(categories))
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# Electowiki queries
# ---------------------------------------------------------------------------

def _electowiki_page_exists(title: str) -> bool:
    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "formatversion": "2",
    }
    url = ELECTOWIKI_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": mwsync.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Error: failed to query Electowiki for '{title}': {e}",
              file=sys.stderr)
        sys.exit(1)
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return False
    return not pages[0].get("missing", False)


def _electowiki_article_url(key: str) -> str:
    return f"{ELECTOWIKI_BASE}/wiki/{urllib.parse.quote(key, safe='/')}"


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def _load_or_minimal_config(config_path: str) -> dict:
    if os.path.exists(config_path):
        return mwsync.load_config(config_path)
    return mwsync.minimal_config()


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="ledecopy.py",
        description=("Copy an enwiki article's lede into an "
                     "mwsync-compatible Electowiki draft."),
    )
    ap.add_argument("title", help='enwiki page title (e.g. "New York")')
    args = ap.parse_args()

    config_path = mwsync.DEFAULT_CONFIG_PATH
    key, title, local_filename = mwsync._parse_article_name(args.title)

    if os.path.exists(local_filename):
        print(f"Error: local file '{local_filename}' already exists.",
              file=sys.stderr)
        print("Refusing to overwrite. Move or remove it before re-running.",
              file=sys.stderr)
        sys.exit(1)

    config = _load_or_minimal_config(config_path)
    articles = config.setdefault("wiki", {}).setdefault("articles", {})
    if key in articles:
        print(f"Error: article '{key}' is already registered in {config_path}.",
              file=sys.stderr)
        print("Use mwsync.py to manage this article instead.", file=sys.stderr)
        sys.exit(1)

    try:
        page = mwsync._fetch_page(title, ENWIKI_API)
    except Exception as e:
        print(f"Error: failed to fetch '{title}' from enwiki: {e}",
              file=sys.stderr)
        sys.exit(1)

    is_redirect, target = _is_redirect(page["wikitext"])
    if is_redirect:
        print(f"Error: '{title}' on enwiki is a redirect"
              + (f" to '{target}'." if target else "."), file=sys.stderr)
        if target:
            print(f"Re-run with the target title:  ledecopy.py \"{target}\"",
                  file=sys.stderr)
        sys.exit(1)

    if _electowiki_page_exists(title):
        print(f"Error: Electowiki article '{title}' already exists.",
              file=sys.stderr)
        print("Add and fetch it with mwsync.py instead:", file=sys.stderr)
        print(f"  mwsync.py add {_electowiki_article_url(key)}",
              file=sys.stderr)
        sys.exit(1)

    cleaned = _strip_top_templates(page["wikitext"])
    lede = _split_lede(cleaned)
    has_refs = _has_refs(lede)
    categories = _extract_categories(page["wikitext"])
    output = _build_output(title, lede, has_refs, page["revid"], categories)

    if not mwsync._atomic_write(local_filename, output):
        sys.exit(1)

    articles[key] = {
        "title": title,
        "url": _electowiki_article_url(key),
        "local": local_filename,
    }
    if not mwsync.save_config(config, config_path):
        sys.exit(1)

    print(f"Imported \"{title}\" from enwiki revision {page['revid']}.")
    if categories:
        print(f"  {len(categories)} category link(s) copied.")
    else:
        print("  No categories found in source.")
    if has_refs:
        print("  References section appended; named refs defined outside the "
              "lede may need review.")
    else:
        print("  No <ref> tags in lede; references section omitted.")
    print(f"  Wrote {local_filename} and updated {config_path}.")
    print()
    print("Next:")
    print(f"  mwsync.py push --new {key} "
          f"-m \"Import lede from [[wikipedia:{title}]]\"")


if __name__ == "__main__":
    main()
