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
entry in mwsync.yaml. The resulting draft is ready for `mwsync.py push --new`.

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
CATMAP_PATH = "catmap.yaml"

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
# Category resolution (catmap.yaml + cache + interactive prompts)
# ---------------------------------------------------------------------------

def _format_category(name: str, sortkey: str | None) -> str:
    if sortkey is not None:
        return f"[[Category:{name}|{sortkey}]]"
    return f"[[Category:{name}]]"


def _load_catmap() -> dict[str, object]:
    """Load catmap.yaml as {normalized_name: target_or_None}.

    Returns an empty dict if the file does not exist. Validates that every
    value is a string or null and exits on malformed entries.
    """
    if not os.path.exists(CATMAP_PATH):
        return {}
    try:
        import yaml
        with open(CATMAP_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"Error reading {CATMAP_PATH}: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        print(f"Error: {CATMAP_PATH}: top-level must be a mapping.", file=sys.stderr)
        sys.exit(1)
    mappings = data.get("mappings") or {}
    if not isinstance(mappings, dict):
        print(f"Error: {CATMAP_PATH}: 'mappings' must be a mapping.", file=sys.stderr)
        sys.exit(1)
    out: dict[str, object] = {}
    for k, v in mappings.items():
        if v is not None and not isinstance(v, str):
            print(f"Error: {CATMAP_PATH}: mapping for {k!r} must be string or null "
                  f"(got {type(v).__name__}).", file=sys.stderr)
            sys.exit(1)
        normalized_key = catmgr.normalize_category_name(str(k))
        if not normalized_key:
            continue
        out[normalized_key] = v if v is None else catmgr.normalize_category_name(v)
    return out


def _save_catmap(mapping: dict[str, object]) -> None:
    sorted_keys = sorted(mapping.keys(), key=str.lower)
    body = {"mappings": {k: mapping[k] for k in sorted_keys}}
    if not mwsync.save_config(body, CATMAP_PATH):
        sys.exit(1)


def _load_category_cache() -> tuple[set[str], set[str], dict[str, str]] | None:
    """Return (canonical_pages, used_categories, redirects) or None if missing.

    canonical_pages: non-redirect category pages on the target wiki.
    used_categories: category names appearing in the allcategories table.
    redirects: {redirect_name: target_name} for category-page redirects with
        a known target.
    """
    if not os.path.exists(catmgr.MANIFEST_PATH):
        return None
    canonical: set[str] = set()
    used: set[str] = set()
    redirects: dict[str, str] = {}

    if os.path.exists(catmgr.CATEGORY_PAGES_PATH):
        try:
            with open(catmgr.CATEGORY_PAGES_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    name = row.get("name")
                    if not isinstance(name, str) or not name:
                        continue
                    if row.get("redirect"):
                        target = row.get("redirect_target")
                        if isinstance(target, str) and target:
                            redirects[name] = target
                    else:
                        canonical.add(name)
        except Exception as e:
            print(f"Warning: could not read {catmgr.CATEGORY_PAGES_PATH}: {e}",
                  file=sys.stderr)
            return None

    if os.path.exists(catmgr.ALLCATEGORIES_PATH):
        try:
            with open(catmgr.ALLCATEGORIES_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    name = row.get("name")
                    if isinstance(name, str) and name:
                        used.add(name)
        except Exception as e:
            print(f"Warning: could not read {catmgr.ALLCATEGORIES_PATH}: {e}",
                  file=sys.stderr)
            return None

    return canonical, used, redirects


def _resolve_redirect(name: str,
                      redirects: dict[str, str]) -> tuple[str, bool]:
    """If `name` is a known redirect, walk the chain to its target.

    Returns (resolved_name, was_redirected). Caps walk at 8 hops and gives up
    on cycles, returning the original name unchanged in that case.
    """
    seen: set[str] = set()
    current = name
    for _ in range(8):
        if current not in redirects or current in seen:
            break
        seen.add(current)
        current = redirects[current]
    return current, current != name


def _print_redirect_note(source: str, target: str) -> None:
    print(f'  "{source}" is a redirect on Electowiki to "{target}"; '
          f'using "{target}".')


def _input_with_completion(prompt: str, candidates: set[str]) -> str:
    """Read a line from stdin with case-insensitive prefix completion against
    `candidates`. Falls back to plain `input()` if `readline` is unavailable.
    """
    try:
        import readline
    except ImportError:
        return input(prompt)

    sorted_candidates = sorted(candidates, key=str.lower)

    def completer(text: str, state: int) -> str | None:
        text_lower = text.lower()
        matches = [c for c in sorted_candidates
                   if c.lower().startswith(text_lower)]
        return matches[state] if state < len(matches) else None

    old_completer = readline.get_completer()
    old_delims = readline.get_completer_delims()
    readline.set_completer(completer)
    readline.set_completer_delims("")
    readline.parse_and_bind("tab: complete")
    try:
        return input(prompt)
    finally:
        readline.set_completer(old_completer)
        readline.set_completer_delims(old_delims)


def _prompt_category_action(name: str, sortkey: str | None,
                            cache_status: str,
                            candidates: set[str]) -> tuple[str, str | None]:
    """Prompt the user for a category decision.

    Returns (action, target). Action is one of: 'map', 'drop', 'keep_save',
    'keep_once', 'skip'. Target is the rename name when action == 'map',
    None otherwise. `candidates` feeds tab completion on the rename input.
    """
    sortkey_note = f"  (sortkey: {sortkey!r})" if sortkey else ""
    print()
    print(f"Category not resolved: {name}{sortkey_note}")
    print(f"  cache: {cache_status}")
    print("  [m] map and save  [d] drop and save  [K] keep and save")
    print("  [k] keep once     [s] skip once")
    while True:
        try:
            choice = input("  choice: ").strip()
        except EOFError:
            return "skip", None
        if choice == "m":
            try:
                new_name = _input_with_completion(
                    "  new category name: ", candidates).strip()
            except EOFError:
                return "skip", None
            if not new_name:
                print("  empty name; please pick again.")
                continue
            return "map", catmgr.normalize_category_name(new_name)
        if choice == "d":
            return "drop", None
        if choice == "K":
            return "keep_save", None
        if choice == "k":
            return "keep_once", None
        if choice == "s":
            return "skip", None
        print("  unrecognized choice; valid options: m, d, K, k, s")


def _category_cache_status(name: str,
                           canonical_pages: set[str] | None,
                           used_categories: set[str] | None) -> str:
    if canonical_pages is None:
        return "cache missing"
    if used_categories is not None and name in used_categories:
        return "used on Electowiki but no category page"
    return "absent from Electowiki cache"


def _category_plan_lines(source_links: list[tuple[str, str | None]],
                         catmap: dict[str, object],
                         cache: tuple[set[str], set[str], dict[str, str]] | None,
                         is_tty: bool) -> list[str]:
    """Describe source-category handling before interactive prompts begin."""
    if cache is None:
        canonical_pages = used_categories = None
        redirects: dict[str, str] = {}
    else:
        canonical_pages, used_categories, redirects = cache

    rows = []
    unresolved: set[str] = set()
    for raw_name, sortkey in source_links:
        normalized = catmgr.normalize_category_name(raw_name)
        if not normalized:
            continue

        sortkey_note = f" | sortkey={sortkey!r}" if sortkey else ""
        if normalized in catmap:
            value = catmap[normalized]
            if value is None:
                disposition = "drop (catmap.yaml)"
            else:
                value_str = str(value)
                resolved, via_redir = _resolve_redirect(value_str, redirects)
                if value_str == normalized and not via_redir:
                    disposition = "keep (catmap.yaml)"
                elif via_redir:
                    disposition = (f"use {resolved} "
                                   f"(catmap.yaml via Electowiki redirect)")
                else:
                    disposition = f"use {value_str} (catmap.yaml)"
        elif normalized in redirects:
            resolved, _via_redir = _resolve_redirect(normalized, redirects)
            disposition = f"use {resolved} (Electowiki redirect)"
        elif canonical_pages is not None and normalized in canonical_pages:
            disposition = "keep (Electowiki category page)"
        else:
            cache_status = _category_cache_status(
                normalized, canonical_pages, used_categories)
            if is_tty:
                disposition = f"ask ({cache_status})"
                unresolved.add(normalized)
            else:
                disposition = f"drop; review-needed ({cache_status})"

        rows.append(f"  - {normalized}{sortkey_note}: {disposition}")

    if not rows:
        return ["Source categories: none."]

    lines = [f"Source categories ({len(rows)}):"]
    lines.extend(rows)
    if is_tty and unresolved:
        suffix = "" if len(unresolved) == 1 else "s"
        lines.append(f"Interactive category decisions needed: "
                     f"{len(unresolved)} unique category name{suffix}.")
    return lines


def _resolve_categories(source_links: list[tuple[str, str | None]],
                        catmap: dict[str, object],
                        cache: tuple[set[str], set[str], dict[str, str]] | None,
                        is_tty: bool) -> tuple[list[str], list[dict], int]:
    """Resolve each source category against catmap and (optional) cache.

    Returns (output_categories, outcomes, new_entries_written). Mutates
    catmap in place; saves catmap.yaml after each new recorded decision so
    that an interrupted prompt session preserves the decisions already made.
    Every emitted category name is routed through the cache's redirect map,
    so a redirect category is never written into the local draft.
    """
    output: list[str] = []
    outcomes: list[dict] = []
    new_entries = 0
    cache_warned = False

    if cache is None:
        canonical_pages = used_categories = None
        redirects: dict[str, str] = {}
    else:
        canonical_pages, used_categories, redirects = cache

    # Tab-completion candidates: cache contents (canonical pages, used
    # categories, redirect names for discovery, redirect targets for
    # canonical names) plus existing rename targets in catmap. Updated live
    # as the user records new mappings during this run.
    candidates: set[str] = set()
    if canonical_pages:
        candidates |= canonical_pages
    if used_categories:
        candidates |= used_categories
    candidates |= set(redirects.keys())
    candidates |= set(redirects.values())
    candidates |= {v for v in catmap.values() if isinstance(v, str)}

    def _emit(name: str, sortkey: str | None,
              outcome: dict, source_for_note: str | None = None) -> None:
        """Append a category to the output, resolving redirects on the way."""
        resolved, via_redir = _resolve_redirect(name, redirects)
        if via_redir:
            _print_redirect_note(source_for_note or name, resolved)
            outcome["via_redirect"] = True
            outcome["target"] = resolved
        output.append(_format_category(resolved, sortkey))

    for raw_name, sortkey in source_links:
        normalized = catmgr.normalize_category_name(raw_name)
        if not normalized:
            continue

        # 1. catmap recorded decision wins.
        if normalized in catmap:
            value = catmap[normalized]
            if value is None:
                outcomes.append({"name": normalized, "action": "drop"})
                continue
            value_str = str(value)
            action = "keep" if value_str == normalized else "map"
            outcome = {"name": normalized, "action": action,
                       "target": value_str}
            _emit(value_str, sortkey, outcome, source_for_note=value_str)
            outcomes.append(outcome)
            continue

        # 2. Source name is itself a known redirect on Electowiki.
        # Substitute the target on emit; do not prompt and do not write a
        # catmap entry — the redirect is the routing rule.
        if normalized in redirects:
            outcome = {"name": normalized, "action": "keep"}
            _emit(normalized, sortkey, outcome, source_for_note=normalized)
            outcomes.append(outcome)
            continue

        # 3. Cache implicit-keep (canonical, non-redirect page).
        if canonical_pages is not None and normalized in canonical_pages:
            outcome = {"name": normalized, "action": "keep"}
            _emit(normalized, sortkey, outcome)
            outcomes.append(outcome)
            continue

        # 4. Determine cache status hint for prompt or report.
        if canonical_pages is None and not cache_warned:
            print("Category cache not found; run catmgr.py fetch for "
                  "better suggestions.", file=sys.stderr)
            cache_warned = True
        cache_status = _category_cache_status(
            normalized, canonical_pages, used_categories)

        # 5. Non-TTY: drop and report, do not prompt.
        if not is_tty:
            outcomes.append({"name": normalized, "action": "review"})
            continue

        # 6. Interactive prompt.
        action, target = _prompt_category_action(normalized, sortkey,
                                                 cache_status, candidates)

        if action == "map":
            resolved, via_redir = _resolve_redirect(target, redirects)
            if via_redir:
                _print_redirect_note(target, resolved)
            output.append(_format_category(resolved, sortkey))
            catmap[normalized] = resolved
            _save_catmap(catmap)
            new_entries += 1
            candidates.add(resolved)
            outcome = {"name": normalized, "action": "map", "target": resolved}
            if via_redir:
                outcome["via_redirect"] = True
            outcomes.append(outcome)
        elif action == "drop":
            catmap[normalized] = None
            _save_catmap(catmap)
            new_entries += 1
            outcomes.append({"name": normalized, "action": "drop"})
        elif action == "keep_save":
            output.append(_format_category(normalized, sortkey))
            catmap[normalized] = normalized
            _save_catmap(catmap)
            new_entries += 1
            candidates.add(normalized)
            outcomes.append({"name": normalized, "action": "keep"})
        elif action == "keep_once":
            output.append(_format_category(normalized, sortkey))
            outcomes.append({"name": normalized, "action": "keep"})
        elif action == "skip":
            outcomes.append({"name": normalized, "action": "skip"})

    return output, outcomes, new_entries


def _category_summary_lines(outcomes: list[dict], new_entries: int) -> list[str]:
    if not outcomes:
        return ["  No categories found in source."]
    counts = {"keep": 0, "map": 0, "drop": 0, "skip": 0, "review": 0}
    redirect_count = 0
    for o in outcomes:
        action = o.get("action")
        if action in counts:
            counts[action] += 1
        if o.get("via_redirect"):
            redirect_count += 1
    parts = []
    if counts["keep"]:
        parts.append(f"{counts['keep']} kept")
    if counts["map"]:
        parts.append(f"{counts['map']} mapped")
    if counts["drop"]:
        parts.append(f"{counts['drop']} dropped")
    if counts["skip"]:
        parts.append(f"{counts['skip']} skipped")
    if counts["review"]:
        parts.append(f"{counts['review']} review-needed")
    lines = [f"  Categories: {', '.join(parts)}."]
    if redirect_count:
        suffix = "" if redirect_count == 1 else "s"
        lines.append(f"  {redirect_count} routed via Electowiki redirect{suffix}.")
    if new_entries:
        suffix = "y" if new_entries == 1 else "ies"
        lines.append(f"  Wrote {new_entries} new catmap.yaml entr{suffix}.")
    if counts["review"]:
        lines.append("  Review-needed (re-run interactively or edit catmap.yaml):")
        for o in outcomes:
            if o.get("action") == "review":
                lines.append(f"    - {o['name']}")
    return lines


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
            by_name[name] = _format_category(name, sortkey)

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


def run_default(args, config_path: str) -> None:
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

    source_links = _extract_category_links(page["wikitext"])
    catmap = _load_catmap()
    cache = _load_category_cache()
    is_tty = sys.stdin.isatty()
    for line in _category_plan_lines(source_links, catmap, cache, is_tty):
        print(line)
    resolved_categories, outcomes, new_entries = _resolve_categories(
        source_links, catmap, cache, is_tty)

    output = _build_output(title, lede, has_refs, page["revid"],
                           resolved_categories)

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
    for line in _category_summary_lines(outcomes, new_entries):
        print(line)
    if has_refs:
        print("  References section appended; named refs defined outside the "
              "lede may need review.")
    else:
        print("  No <ref> tags in lede; references section omitted.")
    print(f"  Wrote {local_filename} and updated {config_path}.")
    print()
    print("Next:")
    print(f"  mwsync.py push --new {key} "
          f"-m \"Import lede from [[wikipedia:{title}]] "
          f"(oldid={page['revid']})\"")


def run_merge(args, config_path: str) -> None:
    derived_key, title, derived_local = mwsync._parse_article_name(args.title)

    if not os.path.exists(config_path):
        print(f"Error: --merge requires an existing {config_path}.",
              file=sys.stderr)
        sys.exit(1)
    config = mwsync.load_config(config_path)
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
    catmap = _load_catmap()
    cache = _load_category_cache()
    for line in _category_plan_lines(source_links, catmap, cache, is_tty):
        print(line)
    resolved_categories, outcomes, new_entries = _resolve_categories(
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
    for line in _category_summary_lines(outcomes, new_entries):
        print(line)
    if has_refs:
        print("  References section appended; named refs defined outside "
              "the lede may need review.")
    else:
        print("  No <ref> tags in lede; references section omitted.")
    print(f"  {config_path} and _cache/ left unchanged.")
    print()
    print("Next:")
    print(f"  mwsync.py push {derived_key} "
          f"-m \"Merge lede from [[wikipedia:{title}]] "
          f"(oldid={page['revid']})\"")


if __name__ == "__main__":
    main()
