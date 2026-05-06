# mwsync.py Architecture

`mwsync.py` is a single-file Python CLI for syncing individual MediaWiki articles with local `.mw` files. It is designed around a small amount of YAML state, a per-article flat-file revision cache, and direct MediaWiki API calls using the Python standard library.

## Runtime Model

The tool expects to run in a working directory that contains `mwsync.yaml`. That config file is the source of truth for registered articles and wiki settings. The top-level shape is:

```yaml
wiki:
  api_base: https://electowiki.org/w/api.php
  articles:
    Article_Key:
      title: Article Title
      url: https://example.org/wiki/Article_Key
      local: Article_Key.mw
      upstream_revid: 123
      upstream_timestamp: "2026-03-18T05:04:08Z"
      upstream_editor: Username
      upstream_summary: Edit summary
      upstream_sha1: hash
      last_pushed_revid: 124
      last_pushed_at: "2026-03-18T05:04:08Z"
```

Each article has three important identities:

- The article key, used as the canonical entry name under `wiki.articles`.
- The MediaWiki page title, used for API queries and edits.
- The local filename, usually `<Article_Key>.mw`, used for the editable working copy.

`resolve_article_entry()` normalizes user input. A command argument may be either the canonical article key or the configured local filename. The function returns both the canonical key and the article entry so downstream code can build cache paths consistently.

## Local Files and Cache

For a registered article, the local working copy lives at the entry's `local` path. Cached upstream state lives under one `_cache/<Article_Key>/` directory per article:

```text
_cache/<Article_Key>/history.jsonl
_cache/<Article_Key>/refs/upstream
_cache/<Article_Key>/refs/base
_cache/<Article_Key>/refs/last-pushed
_cache/<Article_Key>/<revid>.mw
_cache/<Article_Key>/<revid>.json
```

The local file is intended for user edits. Revid-named `.mw` files are cached upstream revision bodies; matching `.json` sidecars store revision metadata. `history.jsonl` is the chronological manifest, while `refs/upstream`, `refs/base`, and `refs/last-pushed` hold small sync-state pointers. Writes use `_atomic_write()`, which writes to a temporary file in the target directory and then replaces the destination.

The older `_cache/server--<Article_Key>.mw` layout is treated as legacy. Current code detects that file and exits with a migration/reset message instead of reading it as normal state.

## Config Helpers

`load_config()` loads `mwsync.yaml` with `PyYAML` and exits with a direct CLI error if the file is missing or invalid. `save_config()` writes YAML atomically using a temporary file and `os.replace()`. `get_api_base()` reads `wiki.api_base`, falling back to the Electowiki API URL.

These helpers deliberately terminate on unrecoverable CLI configuration errors rather than raising exceptions for the command layer to catch.

## MediaWiki API Layer

`mwsync.py` uses the MediaWiki Action API, not the MediaWiki REST API. The configured endpoint is `w/api.php`, and each request passes an `action=...` parameter such as `action=query`, `action=login`, or `action=edit`. A REST API integration would instead use route-shaped endpoints such as `/w/rest.php/...` or `/wiki/rest.php/...`.

As of May 2026, the Action API itself is not broadly deprecated. MediaWiki documentation describes it as unversioned and expected to remain relatively stable. The MediaWiki REST API is also active, but some REST-related Wikimedia services and endpoints have recent or upcoming deprecations. Those include RESTBase, the API Portal's `api.wikimedia.org` routes, and specific REST endpoint variants such as trailing-slash Transform endpoints. Those deprecations do not apply directly to `mwsync.py` because it uses `w/api.php`.

The API functions are small wrappers around `urllib.request`:

- `_fetch_page()` calls `action=query` with `prop=revisions` and returns wikitext plus revision metadata.
- `_mw_login()` performs the MediaWiki bot-password login flow and returns an opener with cookies.
- `_mw_get_csrf_token()` fetches an edit token using the authenticated opener.
- `_mw_edit_page()` submits `action=edit`, using `baserevid` for existing pages or `createonly` for new pages.

All requests set the shared `USER_AGENT`. Network errors and MediaWiki errors are converted into clear exceptions, which command handlers catch and print to stderr.

## Command Flow

`main()` defines the CLI with `argparse`, loads config once, and dispatches to one `run_*` handler.

`add` parses a `/wiki/` URL, derives the page title and article key, then inserts a new article entry into `mwsync.yaml`. It does not fetch page content.

`checkout` is the bootstrap convenience command. With a URL, it registers the article if needed, fetches upstream cache state, and merges the fetched upstream revision into the local `.mw` file. With `ARTICLE@REV --to PATH`, it writes that cached or fetchable revision body to a separate path without changing refs.

`fetch` resolves the article, fetches the current server revision, writes `_cache/<Article_Key>/<revid>.mw`, `_cache/<Article_Key>/<revid>.json`, `history.jsonl`, and `refs/upstream`, then leaves the local `.mw` file unchanged. It records metadata for the newest 50 revisions by default without downloading every old revision body; `--depth N` changes that metadata window, `--all-known` walks all available revision metadata, and `--with-bodies` fetches bodies for the selected metadata window.

`merge` reconciles the local working file with fetched upstream state. It uses `refs/base` as the common ancestor, `refs/upstream` as the remote side, and the local `.mw` file as the local side. A clean merge or fast-forward updates `refs/base`; a conflict writes conflict markers and leaves `refs/base` unchanged.

`push` resolves the article, reads the local file, obtains an edit summary from `-m/--message` or `$VISUAL`/`$EDITOR`, logs in with `MWSYNC_MW_USER` and `MWSYNC_MW_PASSWORD`, submits the edit, records push metadata, updates `refs/last-pushed`, then re-fetches the page to resync the local file, cache, `refs/upstream`, and `refs/base`.

`diff` compares cached revisions and local files using `git diff --no-index`. `diff New_York` compares `New_York@upstream` with the local working file. `diff New_York@upstream^ New_York@upstream` compares two cached revision expressions. With `--remote`, it first refreshes the upstream cache without rewriting the local working copy.

`difftool` launches `meld` against `New_York@upstream` and the local file.

`log` prints cached revision history from `history.jsonl`. If the earliest cached revision still points to a parent revision that is not present locally, `log` prints an incomplete-history note before the revision list.

`show` prints revision text for expressions such as `New_York@upstream`, `New_York@upstream^`, or `New_York@19778`. If metadata is known but the requested body is not cached yet, `show` fetches that one revision body by revid and stores it in the article cache.

`status` prints tracked article state, including local path, git cleanliness, upstream revision metadata, refs, and last pushed revision.

`fsck` checks cache consistency for one article or all registered articles. It reports legacy cache files, malformed refs, missing revision bodies or sidecars, non-chronological history entries, and ref/history mismatches. It does not repair files implicitly.

## Error Handling and Safety

The script is CLI-oriented: most user-facing failures print to stderr and call `sys.exit(1)`. This keeps command behavior predictable but means internal functions are not pure library APIs.

The main safety checks are:

- `fetch` does not overwrite local content; `merge` is responsible for changing the working `.mw` file.
- `push` requires an upstream revision unless `--new` is specified.
- `push` uses `baserevid` so MediaWiki can detect edit conflicts.
- Legacy `_cache/server--<Article_Key>.mw` files are detected and produce a clear migration/reset error.
- `show`, `diff`, and revision checkout fetch missing old revision bodies on demand when the history metadata identifies the requested revid.

## External Dependencies

The script requires Python 3 and `PyYAML`. It shells out to `git` for modification checks and diffs, and to `meld` for visual diffs. Push operations require MediaWiki bot credentials in environment variables:

```bash
export MWSYNC_MW_USER='User@BotName'
export MWSYNC_MW_PASSWORD='bot-password'
```

No persistent cookies or tokens are stored by the script.
