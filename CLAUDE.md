# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`mwsync.py` is a single-file Python 3 CLI that syncs individual MediaWiki articles between a local `.mw` working copy and a remote wiki (default: Electowiki). Command shape and refs are deliberately git-like (`fetch` / `merge` / `push` / `diff` / `log` / `show` / `checkout`).

There is no build system. Commands run against the script directly:

```bash
python3 mwsync.py --help                           # usage
python3 mwsync.py status                           # tracked articles + sync state
python3 mwsync.py add Maine                        # register by page name (no fetch)
python3 mwsync.py checkout https://electowiki.org/wiki/Maine  # register + fetch + merge
python3 mwsync.py fetch Maine                      # update _cache only, never local
python3 mwsync.py merge Maine                      # reconcile upstream → local
python3 mwsync.py diff Maine                       # upstream vs local (uses git diff --no-index)
python3 mwsync.py push Maine -m "summary"          # send local to wiki, then re-fetch
python3 mwsync.py fsck                             # check cache + ref consistency
python3 -m py_compile mwsync.py                    # syntax check before commit
```

Push requires `MWSYNC_MW_USER` and `MWSYNC_MW_PASSWORD` (MediaWiki bot credentials) in the environment. Diff/difftool shell out to `git` and `meld` respectively. Only runtime dep beyond the standard library is `PyYAML`.

## Architecture

Read `docs/architecture-mwsync.md` first — it is current and covers the runtime model in detail. `docs/roadmap.md` describes the cache redesign that mainline now implements; `docs/legacy.md` describes the older `_cache/server--<Article_Key>.mw` snapshot format that is no longer read as normal state.

Things that aren't obvious from one file alone:

- **Three identities per article.** The *article key* (e.g. `New_York`) is the canonical entry name under `wiki.articles` in `mwsync.yaml` and is used to derive every cache path. The *MediaWiki page title* (`New York`) is what API calls use. The *local filename* (`New_York.mw`) is the editable working copy. `resolve_article_entry()` accepts either the key or the local filename and always returns the canonical key — downstream code must use the returned key to build cache paths, never the raw user argument.
- **Per-article cache layout.** Each article owns `_cache/<Article_Key>/` containing `history.jsonl` (chronological revision ledger, oldest → newest), revid-named bodies (`19778.mw`) with metadata sidecars (`19778.json`), and three single-revid ref files under `refs/`: `upstream` (latest fetched), `base` (revision the local file is based on), `last-pushed` (last successful push from this checkout). `latest` is intentionally not a single file — sync code needs those three states separated.
- **Fetch never touches the local working file.** `fetch` writes cache + `refs/upstream` only. `merge` is the sole code path that rewrites `<Article_Key>.mw`. `push` re-fetches after a successful edit to resync `refs/upstream` and `refs/base`.
- **Depth is metadata-only by default.** `fetch --depth N` records metadata for N revisions but only guarantees the latest body. Use `--with-bodies` to actually download them. `show`/`diff`/`checkout` will fetch a missing single body on demand by revid.
- **Revision expressions.** `Article@upstream`, `Article@upstream^`, `Article@upstream~5`, `Article@<revid>` resolve via `history.jsonl` + refs. `Article.mw` refers to the local working file. These are parsed in `_resolve_revision_arg()`.
- **MediaWiki Action API, not REST.** All API calls go through `w/api.php` with `action=query|login|edit`. Do not introduce REST endpoints (`/w/rest.php/...`) without a deliberate reason — the Action API choice is documented in `docs/architecture-mwsync.md`.
- **CLI-style error handling.** Config helpers (`load_config`, `save_config`, `get_api_base`) and many resolvers print to stderr and `sys.exit(1)` on failure rather than raising. Treat them as terminal, not library APIs.
- **Atomic writes.** All cache + config writes go through `_atomic_write()` / temp-file + `os.replace()`. Preserve this when adding new write paths.
- **Legacy boundary.** If `_cache/server--<Article_Key>.mw` exists without the new layout, code stops with a migration message (`_check_legacy_cache`). Do not add code that reads the legacy snapshot as normal state.

## ledecopy.py

`ledecopy.py` is a sister tool that copies an English Wikipedia article's lede into an `mwsync`-compatible local draft (`{{Wikipedia|...}}` header + cleaned lede + `{{Fromwikipedia|...|oldid=...}}` attribution + categories) and registers the article in `mwsync.yaml` so it is ready for `mwsync.py push --new`.

```bash
python3 ledecopy.py "New York"
```

Spec: `docs/ledecopy.md`. enwiki and Electowiki API endpoints are hardcoded.

Things to know when working on it:

- **Imports from `mwsync.py` directly**, including private-named helpers (`_parse_article_name`, `_atomic_write`, `_fetch_page`, plus `load_config` / `save_config` / `minimal_config` / `USER_AGENT`). The spec calls this out as intentional — they share state via `mwsync.yaml` and are not a public/private boundary.
- **No override flag.** Pre-flight refuses if the local `.mw` exists, the article key is already in `mwsync.yaml`, the enwiki source is a redirect, or the Electowiki page exists. Adding `--force` is in Future Directions; don't add one without a spec change.
- **Top-of-page template stripper is intentionally narrow.** A small allowlist (`STRIP_EXACT_NAMES` plus `infobox ` and `pp-` prefixes), brace-matched, and stops at the first non-template, non-comment, non-whitespace token. The spec frames this as conservative on purpose — replacing it with a real wikitext parser is in Future Directions.
- **HTML-comment passthrough in the stripper.** Top-of-page `<!-- ... -->` runs are skipped so a comment doesn't block detection of the infobox below it. Preserve this when touching `_strip_top_templates`.
- **Categories come from the full source**, not the stripped lede — they live at the bottom of the wiki source, after the lede split point.

## Conventions

`AGENTS.md` is the source of truth for commit message style, code style, and PR expectations. Honor it — don't restate it here. Notably: short imperative commit subjects (e.g. `Add fetch dry-run guard - preserve local edits`), 4-space Python indentation, `snake_case`, keep dependencies to stdlib + PyYAML.

There is no automated test suite. For changes, at minimum run `python3 -m py_compile mwsync.py ledecopy.py` and exercise the affected subcommand, ideally with `--dry-run` where supported.
