# mwsync.py Architecture

`mwsync.py` is a single-file Python CLI for syncing individual MediaWiki articles with local `.mw` files. It is designed around a small amount of YAML state, a cached copy of the last fetched server revision, and direct MediaWiki API calls using the Python standard library.

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

## Local Files

For a registered article, the local working copy lives at the entry's `local` path. Server snapshots live under `_cache/` with the naming pattern:

```text
_cache/server--<article-key>.mw
```

The local file is intended for user edits. The snapshot is the last known upstream text and is used by `diff` and `difftool` as the comparison base. Writes use `_atomic_write()`, which writes to a temporary file in the target directory and then replaces the destination.

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

`fetch` resolves the article, refuses to overwrite an uncommitted local file unless `--force` is used, fetches the current server revision, writes both the local `.mw` file and `_cache` snapshot, then updates upstream metadata in `mwsync.yaml`.

`push` resolves the article, reads the local file, obtains an edit summary from `-m/--message` or `$VISUAL`/`$EDITOR`, logs in with `MWSYNC_MW_USER` and `MWSYNC_MW_PASSWORD`, submits the edit, records push metadata, then re-fetches the page to resync the local file and server snapshot.

`diff` compares the cached server snapshot with the local file using `git diff --no-index`. With `--remote`, it first refreshes the server snapshot without rewriting the local working copy.

`difftool` launches `meld` against the cached server snapshot and the local file.

`status` prints tracked article state, including local path, git cleanliness, upstream revision metadata, and last pushed revision.

## Error Handling and Safety

The script is CLI-oriented: most user-facing failures print to stderr and call `sys.exit(1)`. This keeps command behavior predictable but means internal functions are not pure library APIs.

The main safety checks are:

- `fetch` checks `git status --porcelain -- <local>` before overwriting local content.
- `push` requires an upstream revision unless `--new` is specified.
- `push` uses `baserevid` so MediaWiki can detect edit conflicts.
- `diff` and `difftool` require an existing server snapshot and tell the user to run `fetch` when missing.

## External Dependencies

The script requires Python 3 and `PyYAML`. It shells out to `git` for modification checks and diffs, and to `meld` for visual diffs. Push operations require MediaWiki bot credentials in environment variables:

```bash
export MWSYNC_MW_USER='User@BotName'
export MWSYNC_MW_PASSWORD='bot-password'
```

No persistent cookies or tokens are stored by the script.
