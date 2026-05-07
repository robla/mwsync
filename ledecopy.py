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


def _load_category_cache() -> tuple[set[str], set[str]] | None:
    """Return (category_pages, used_categories) sets, or None if cache missing."""
    if not os.path.exists(catmgr.MANIFEST_PATH):
        return None
    pages: set[str] = set()
    used: set[str] = set()
    for path, target in ((catmgr.CATEGORY_PAGES_PATH, pages),
                         (catmgr.ALLCATEGORIES_PATH, used)):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    name = row.get("name")
                    if isinstance(name, str) and name:
                        target.add(name)
        except Exception as e:
            print(f"Warning: could not read {path}: {e}", file=sys.stderr)
            return None
    return pages, used


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


def _resolve_categories(source_links: list[tuple[str, str | None]],
                        catmap: dict[str, object],
                        cache: tuple[set[str], set[str]] | None,
                        is_tty: bool) -> tuple[list[str], list[dict], int]:
    """Resolve each source category against catmap and (optional) cache.

    Returns (output_categories, outcomes, new_entries_written). Mutates
    catmap in place; saves catmap.yaml after each new recorded decision so
    that an interrupted prompt session preserves the decisions already made.
    """
    output: list[str] = []
    outcomes: list[dict] = []
    new_entries = 0
    cache_warned = False

    if cache is None:
        category_pages, used_categories = None, None
    else:
        category_pages, used_categories = cache

    # Tab-completion candidates: cache contents plus existing rename targets.
    # Updated live as the user records new mappings during this run.
    candidates: set[str] = set()
    if category_pages:
        candidates |= category_pages
    if used_categories:
        candidates |= used_categories
    candidates |= {v for v in catmap.values() if isinstance(v, str)}

    for raw_name, sortkey in source_links:
        normalized = catmgr.normalize_category_name(raw_name)
        if not normalized:
            continue

        if normalized in catmap:
            value = catmap[normalized]
            if value is None:
                outcomes.append({"name": normalized, "action": "drop"})
                continue
            if value == normalized:
                output.append(_format_category(normalized, sortkey))
                outcomes.append({"name": normalized, "action": "keep"})
                continue
            output.append(_format_category(str(value), sortkey))
            outcomes.append({"name": normalized, "action": "map", "target": value})
            continue

        if category_pages is not None and normalized in category_pages:
            output.append(_format_category(normalized, sortkey))
            outcomes.append({"name": normalized, "action": "keep"})
            continue

        if category_pages is None:
            if not cache_warned:
                print("Category cache not found; run catmgr.py fetch for "
                      "better suggestions.", file=sys.stderr)
                cache_warned = True
            cache_status = "cache missing"
        elif used_categories is not None and normalized in used_categories:
            cache_status = "used on Electowiki but no category page"
        else:
            cache_status = "absent from Electowiki cache"

        if not is_tty:
            outcomes.append({"name": normalized, "action": "review"})
            continue

        action, target = _prompt_category_action(normalized, sortkey,
                                                 cache_status, candidates)

        if action == "map":
            output.append(_format_category(target, sortkey))
            catmap[normalized] = target
            _save_catmap(catmap)
            new_entries += 1
            candidates.add(target)
            outcomes.append({"name": normalized, "action": "map", "target": target})
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
    for o in outcomes:
        action = o.get("action")
        if action in counts:
            counts[action] += 1
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

    source_links = _extract_category_links(page["wikitext"])
    catmap = _load_catmap()
    cache = _load_category_cache()
    is_tty = sys.stdin.isatty()
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
          f"-m \"Import lede from [[wikipedia:{title}]]\"")


if __name__ == "__main__":
    main()
