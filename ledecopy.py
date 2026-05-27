#!/usr/bin/env python3
# Copyright (c) 2026 Rob Lanphier and contributors
# SPDX-License-Identifier: MIT
# See LICENSE for details.
"""ledecopy.py — Copy an enwiki article's lede into an Electowiki draft.

Usage:
  ledecopy.py "New York"
  ledecopy.py --merge "Ohio"

The argument is an enwiki page title. ledecopy fetches the page from English
Wikipedia, extracts the lede (the wikitext before the first level-2 heading),
strips obvious non-prose top-of-page templates, adds Electowiki attribution
templates, and writes the result to a local <Article_Key>.mw file plus an
entry in mwsync.yaml. The resulting draft is ready for `mwsync.py commit --new`.

ledecopy refuses to run if the local file exists, the article key is already
registered in mwsync.yaml, the enwiki source is a redirect, or the target
Electowiki page already exists. There is no override flag.

`--merge`/`-m` splices a fresh enwiki lede into an existing clean local
checkout. The local body above the trailing category/interwiki block is
preserved verbatim; the new lede + attribution chunk is inserted between
that body and the categories; resolved enwiki categories are unioned with
the existing local categories. mwsync.yaml and _cache/ are left untouched
so that `mwsync.py diff` sees the splice as a normal local edit.

Categories from the enwiki source are resolved through `catmap.yaml` in the
working directory. Known mappings apply silently; unknown categories prompt
the user when stdin is a TTY, and answers are recorded so subsequent runs
get faster. With no TTY, unknown categories are dropped and listed as
review-needed in the run summary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

import catmgr
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

# Patterns that recognize a "tail block" line for --merge: blank lines,
# [[Category:...]] at column zero, [[xx:...]] interlanguage links (2-3
# lowercase letters before the colon), and single-line {{...}} templates.
TAIL_CATEGORY_RE = re.compile(r"^\[\[\s*[Cc]ategory\s*:[^\]\n]+\]\]\s*$")
TAIL_LANGCODE_RE = re.compile(r"^\[\[[a-z]{2,3}:[^\]\n]+\]\]\s*$")
TAIL_TEMPLATE_RE = re.compile(r"^\{\{[^\n]*\}\}\s*$")


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
        if source.startswith("<!--", cursor):
            comment_end = source.find("-->", cursor + 4)
            if comment_end < 0:
                cursor = ws_start
                break
            cursor = comment_end + 3
            continue
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


def _extract_category_links(source: str) -> list[tuple[str, str | None]]:
    """Return a list of (raw_name, sortkey) tuples from [[Category:...]] links."""
    result = []
    for link in CATEGORY_RE.findall(source):
        inner = link[2:-2]
        _, _, payload = inner.partition(":")
        if "|" in payload:
            name, sortkey = payload.split("|", 1)
        else:
            name, sortkey = payload, None
        result.append((name.strip(), sortkey))
    return result


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
# Merge mode: tail-block split and category merge
# ---------------------------------------------------------------------------

def _is_tail_block_line(line: str) -> bool:
    if not line.strip():
        return True
    if TAIL_CATEGORY_RE.match(line):
        return True
    if TAIL_LANGCODE_RE.match(line):
        return True
    if TAIL_TEMPLATE_RE.match(line):
        return True
    return False


def _split_body_and_tail(text: str) -> tuple[list[str], list[str]]:
    """Walk backward from EOF to find the trailing tail block.

    Returns (body_lines, tail_lines) where both are line strings without
    trailing newlines. Blank lines, [[Category:...]] lines, [[xx:...]]
    interlanguage links, and single-line {{...}} trailing templates all count
    as tail-block lines; the walk stops at the first non-matching line.
    """
    lines = text.splitlines()
    tail_start = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if _is_tail_block_line(lines[i]):
            tail_start = i
        else:
            break
    return lines[:tail_start], lines[tail_start:]


def _partition_tail(tail_lines: list[str]) -> tuple[
        list[tuple[str, str | None]], list[str]]:
    """Split tail-block lines into (existing_categories, non_category_lines).

    Categories are returned as (normalized_name, sortkey) tuples in source
    order. Blank lines in the tail are dropped. Non-category lines
    (interlanguage links, trailing templates) are returned verbatim so they
    can be re-emitted below the merged category block.
    """
    categories: list[tuple[str, str | None]] = []
    non_cat: list[str] = []
    for line in tail_lines:
        if not line.strip():
            continue
        if TAIL_CATEGORY_RE.match(line):
            for raw_name, sortkey in _extract_category_links(line):
                normalized = catmgr.normalize_category_name(raw_name)
                if normalized:
                    categories.append((normalized, sortkey))
        else:
            non_cat.append(line)
    return categories, non_cat


def _merge_categories(
        existing: list[tuple[str, str | None]],
        resolved: list[str]) -> tuple[list[str], int]:
    """Union existing local categories with resolved enwiki categories.

    Returns (merged_links, new_from_enwiki_count). Existing categories come
    first (preserving their original order and sortkeys); any resolved
    enwiki categories not already present (by normalized name) are appended
    after.
    """
    by_name: dict[str, str] = {}
    for name, sortkey in existing:
        if name not in by_name:
            by_name[name] = catmgr.format_category_link(name, sortkey)

    new_count = 0
    for link in resolved:
        inner = link[2:-2]
        _, _, payload = inner.partition(":")
        name_part = payload.split("|", 1)[0]
        normalized = catmgr.normalize_category_name(name_part)
        if normalized and normalized not in by_name:
            by_name[normalized] = link
            new_count += 1

    return list(by_name.values()), new_count


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
    ap.add_argument(
        "--merge", "-m", action="store_true",
        help=("splice the lede into an existing clean local checkout "
              "instead of creating a new draft"),
    )
    args = ap.parse_args()

    config_path = mwsync.DEFAULT_CONFIG_PATH
    if args.merge:
        run_merge(args, config_path)
    else:
        run_default(args, config_path)


def _article_identity(config: dict, raw_title: str) -> tuple[str, str, str, str]:
    key, fields = mwsync._article_fields_from_title(
        config, raw_title, fetch_namespaces=False,
    )
    return key, fields["title"], fields["local"], fields.get("url", "")


def run_default(args, config_path: str) -> None:
    config = _load_or_minimal_config(config_path)
    key, title, local_filename, article_url = _article_identity(config, args.title)

    if os.path.exists(local_filename):
        print(f"Error: local file '{local_filename}' already exists.",
              file=sys.stderr)
        print("Refusing to overwrite. Move or remove it before re-running.",
              file=sys.stderr)
        sys.exit(1)

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

    source_links = _extract_category_links(page["wikitext"])
    catmap = catmgr.load_catmap()
    cache = catmgr.load_category_cache()
    is_tty = sys.stdin.isatty()
    for line in catmgr.category_plan_lines(source_links, catmap, cache, is_tty):
        print(line)
    resolved_categories, outcomes, new_entries = catmgr.resolve_categories(
        source_links, catmap, cache, is_tty)

    output = _build_output(title, lede, has_refs, page["revid"],
                           resolved_categories)

    if not mwsync._atomic_write(local_filename, output):
        sys.exit(1)

    articles[key] = {
        "title": title,
        "url": article_url or _electowiki_article_url(key),
        "local": local_filename,
    }
    if not mwsync.save_config(config, config_path):
        sys.exit(1)

    print(f"Imported \"{title}\" from enwiki revision {page['revid']}.")
    for line in catmgr.category_summary_lines(outcomes, new_entries):
        print(line)
    if has_refs:
        print("  References section appended; named refs defined outside the "
              "lede may need review.")
    else:
        print("  No <ref> tags in lede; references section omitted.")
    print(f"  Wrote {local_filename} and updated {config_path}.")
    print()
    print("Next:")
    print(f"  mwsync.py commit --new {key} "
          f"-m \"Import lede from [[wikipedia:{title}]] "
          f"(oldid={page['revid']})\"")
    print(f"  mwsync.py push {key}")


def run_merge(args, config_path: str) -> None:
    if not os.path.exists(config_path):
        print(f"Error: --merge requires an existing {config_path}.",
              file=sys.stderr)
        sys.exit(1)
    config = mwsync.load_config(config_path)
    derived_key, title, derived_local, _article_url = _article_identity(config, args.title)
    articles = config.get("wiki", {}).get("articles") or {}
    if derived_key not in articles:
        print(f"Error: article '{derived_key}' is not registered in "
              f"{config_path}.", file=sys.stderr)
        print("Check it out with mwsync.py first:", file=sys.stderr)
        print(f"  mwsync.py checkout {_electowiki_article_url(derived_key)}",
              file=sys.stderr)
        sys.exit(1)

    entry = articles[derived_key]
    local = entry.get("local") if isinstance(entry, dict) else None
    if not isinstance(local, str) or not local:
        print(f"Error: {config_path} entry for '{derived_key}' has no "
              "'local' path.", file=sys.stderr)
        sys.exit(1)
    if local != derived_local:
        print("Error: --merge requires the configured local filename to "
              "match the key derived from the enwiki title.",
              file=sys.stderr)
        print(f"  configured: {local}", file=sys.stderr)
        print(f"  derived:    {derived_local}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(local):
        print(f"Error: local file '{local}' does not exist.", file=sys.stderr)
        sys.exit(1)

    refs_dir = os.path.join("_cache", derived_key, "refs")
    base_ref_path = os.path.join(refs_dir, "base")
    upstream_ref_path = os.path.join(refs_dir, "upstream")
    if not (os.path.exists(base_ref_path)
            and os.path.exists(upstream_ref_path)):
        print(f"Error: --merge requires '{derived_key}' to have been "
              "fetched from Electowiki at least once.", file=sys.stderr)
        print(f"  expected: {base_ref_path} and {upstream_ref_path}",
              file=sys.stderr)
        print(f"  run:  mwsync.py fetch {derived_key}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(base_ref_path, "r", encoding="utf-8") as f:
            base_revid = f.read().strip()
    except Exception as e:
        print(f"Error: failed to read {base_ref_path}: {e}",
              file=sys.stderr)
        sys.exit(1)
    if not base_revid:
        print(f"Error: {base_ref_path} is empty.", file=sys.stderr)
        sys.exit(1)

    base_body_path = os.path.join(
        "_cache", derived_key, f"{base_revid}.mw")
    if not os.path.exists(base_body_path):
        print(f"Error: cached body for refs/base ({base_revid}) is missing.",
              file=sys.stderr)
        print(f"  expected: {base_body_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(local, "rb") as f:
            local_bytes = f.read()
        with open(base_body_path, "rb") as f:
            base_bytes = f.read()
    except Exception as e:
        print(f"Error: failed to read local or base body: {e}",
              file=sys.stderr)
        sys.exit(1)
    if local_bytes != base_bytes:
        print(f"Error: local file '{local}' has uncommitted edits "
              "relative to refs/base.", file=sys.stderr)
        print("  --merge requires a clean checkout. Inspect changes with:",
              file=sys.stderr)
        print(f"    mwsync.py diff {derived_key}", file=sys.stderr)
        print("  Resolve or commit the local edits, then re-run.",
              file=sys.stderr)
        sys.exit(1)

    is_tty = sys.stdin.isatty()
    if not is_tty:
        print("Error: --merge requires an interactive terminal for "
              "confirmation.", file=sys.stderr)
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
            print("Re-run with the target title:  "
                  f"ledecopy.py --merge \"{target}\"", file=sys.stderr)
        sys.exit(1)

    cleaned = _strip_top_templates(page["wikitext"])
    lede = _split_lede(cleaned)
    has_refs = _has_refs(lede)

    source_links = _extract_category_links(page["wikitext"])
    catmap = catmgr.load_catmap()
    cache = catmgr.load_category_cache()
    for line in catmgr.category_plan_lines(source_links, catmap, cache, is_tty):
        print(line)
    resolved_categories, outcomes, new_entries = catmgr.resolve_categories(
        source_links, catmap, cache, is_tty)

    local_text = local_bytes.decode("utf-8")
    body_lines, tail_lines = _split_body_and_tail(local_text)
    existing_cats, non_cat_tail = _partition_tail(tail_lines)
    merged_cats, new_from_enwiki = _merge_categories(
        existing_cats, resolved_categories)

    inserted_chunk = _build_output(
        title, lede, has_refs, page["revid"], []).rstrip("\n")
    inserted_line_count = len(inserted_chunk.splitlines())

    print()
    print(f"About to merge the lede from enwiki revision "
          f"{page['revid']} into {local}.")
    print(f"  Body kept: {len(body_lines)} lines above the original "
          "category block.")
    parts_desc = "{{Wikipedia}} + lede"
    if has_refs:
        parts_desc += " + references"
    parts_desc += " + {{Fromwikipedia}}"
    print(f"  Inserted: {parts_desc} ({inserted_line_count} lines).")
    print(f"  Categories: {len(existing_cats)} existing + "
          f"{new_from_enwiki} new from enwiki, "
          f"{len(merged_cats)} in merged set.")
    if non_cat_tail:
        print(f"  Non-category tail items preserved: {len(non_cat_tail)} "
              "(interlanguage links / trailing templates).")
    try:
        answer = input("Continue? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        print("Aborted; local file unchanged.")
        sys.exit(1)
    if answer not in ("y", "yes"):
        print("Aborted; local file unchanged.")
        sys.exit(0)

    output_blocks: list[str] = []
    body_text = "\n".join(body_lines).rstrip()
    if body_text:
        output_blocks.append(body_text)
    output_blocks.append(inserted_chunk)
    if merged_cats:
        output_blocks.append("\n".join(merged_cats))
    if non_cat_tail:
        output_blocks.append("\n".join(non_cat_tail))
    output = "\n\n".join(output_blocks) + "\n"

    if not mwsync._atomic_write(local, output):
        sys.exit(1)

    print()
    print(f"Merged lede from \"{title}\" (enwiki revision "
          f"{page['revid']}) into {local}.")
    for line in catmgr.category_summary_lines(outcomes, new_entries):
        print(line)
    if has_refs:
        print("  References section appended; named refs defined outside "
              "the lede may need review.")
    else:
        print("  No <ref> tags in lede; references section omitted.")
    print(f"  {config_path} and _cache/ left unchanged.")
    print()
    print("Next:")
    print(f"  mwsync.py commit {derived_key} "
          f"-m \"Merge lede from [[wikipedia:{title}]] "
          f"(oldid={page['revid']})\"")
    print(f"  mwsync.py push {derived_key}")


if __name__ == "__main__":
    main()
