# mwsync.py Architecture

`mwsync.py` is a single-file Python CLI for syncing individual MediaWiki articles with local `.mw` files. It is designed around a small amount of YAML state, a per-article flat-file revision cache, and direct MediaWiki API calls using the Python standard library.

## Runtime Model

The tool expects to run in a working directory that contains `mwsync.yaml`.
Each working directory is dedicated to one MediaWiki instance. The
directory-wide `wiki.api_base` applies to every tracked article, every cache
entry, and every helper tool that shares this checkout. Mixing articles from
different wikis in one `mwsync.yaml` is intentionally out of scope; use a
separate directory for each wiki, such as one checkout for Electowiki and
another for a private wiki.

`mwsync.yaml` is the source of truth for registered articles and wiki
settings. The top-level shape is:

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

`init` should be the only normal command that creates `mwsync.yaml`. Other
subcommands should require an existing config file and fail clearly if the user
is in the wrong directory. This is intentionally stricter than older behavior
where `checkout` could bootstrap a missing config: it prevents accidentally
creating `mwsync.yaml`, `_cache/`, and `.mw` files in an unintended directory.
If a later workflow needs implicit initialization, it should be opt-in and
explicitly named, not a side effect of a normal checkout or fetch.

Because the working directory is dedicated to one wiki, URL inputs must also
match the configured wiki. For example, in a directory whose `wiki.api_base` is
`https://electowiki.org/w/api.php`, `mwsync.py checkout
https://en.wikipedia.org/wiki/Non-negative_responsiveness` should fail with a
clear "wrong wiki" error. It should not silently reinterpret the enwiki URL's
title as `https://electowiki.org/wiki/Non-negative_responsiveness`.

## Local Files and Cache

For a registered article, the local working copy lives at the entry's `local` path. Cached upstream state lives under one `_cache/<Article_Key>/` directory per article:

```text
_cache/<Article_Key>/history.jsonl
_cache/<Article_Key>/refs/upstream
_cache/<Article_Key>/refs/base
_cache/<Article_Key>/refs/last-pushed
_cache/<Article_Key>/commit.json
_cache/<Article_Key>/commit.mw
_cache/<Article_Key>/merge.json
_cache/<Article_Key>/<revid>.mw
_cache/<Article_Key>/<revid>.json
```

The local file is intended for user edits. Revid-named `.mw` files are cached upstream revision bodies; matching `.json` sidecars store revision metadata. `history.jsonl` is the chronological manifest, while `refs/upstream`, `refs/base`, and `refs/last-pushed` hold small sync-state pointers. `commit.mw` and `commit.json` store the single pending local edit that `push` will publish. `merge.json` records an unresolved merge conflict so a later `commit` can use the fetched upstream revid as the MediaWiki `baserevid`. Writes use `_atomic_write()`, which writes to a temporary file in the target directory and then replaces the destination.

The older `_cache/server--<Article_Key>.mw` layout is treated as legacy. Current code detects that file and exits with a migration/reset message instead of reading it as normal state.

Cache subdirectories whose names start with `_` are reserved for wiki-level
indexes rather than article checkouts. The near-term namespace plan is:

```text
_cache/<Article_Key>/        per-article revision cache
_cache/_categories/         target-wiki category index
_cache/_titles/             target-wiki title index
```

The current category helper may still use `_cache/categories/`; the intended
future path is `_cache/_categories/` so wiki-level caches are visually distinct
from article keys. Article keys should not begin with `_` once this convention
is enforced.

## Config Helpers

`load_config()` loads `mwsync.yaml` with `PyYAML` and exits with a direct CLI error if the file is missing or invalid. Normal subcommands should not call `minimal_config()` as a fallback; `minimal_config()` is for `init` and tests. `save_config()` writes YAML atomically using a temporary file and `os.replace()`. `get_api_base()` reads `wiki.api_base`, falling back to the Electowiki API URL.

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

`add` accepts either a `/wiki/` URL or a page name/title. URLs derive the page title, article key, local filename, and stored URL from the URL path, but only after validating that the URL belongs to the configured wiki. Names derive `title`, `Article_Key`, and `Article_Key.mw` locally, then derive a best-effort page URL from the directory-wide `wiki.api_base`. It does not fetch page content.

`checkout` is the convenience command for adding a page to an already-initialized working directory. With a URL or page name, it registers the article if needed, fetches upstream cache state, and merges the fetched upstream revision into the local `.mw` file. It must not create `mwsync.yaml`; users should run `mwsync.py init` first. With `ARTICLE@REV --to PATH`, it writes that cached or fetchable revision body to a separate path without changing refs.

`fetch` resolves the article, fetches the current server revision, writes `_cache/<Article_Key>/<revid>.mw`, `_cache/<Article_Key>/<revid>.json`, `history.jsonl`, and `refs/upstream`, then leaves both the local `.mw` file and `mwsync.yaml` unchanged. It records metadata for the newest 50 revisions by default without downloading every old revision body; `--depth N` changes that metadata window, `--all-known` walks all available revision metadata, and `--with-bodies` fetches bodies for the selected metadata window. Fetch should be transactional at the article-cache level: a failed network call, metadata fetch, body fetch, or validation step should not leave new refs or partially updated manifests pointing at incomplete data.

`merge` reconciles the local working file with fetched upstream state. It uses `refs/base` as the common ancestor, `refs/upstream` as the remote side, and the local `.mw` file as the local side. A clean merge or fast-forward updates `refs/base` and records the local file's upstream base in `mwsync.yaml` using the legacy `upstream_revid`, `upstream_timestamp`, `upstream_editor`, `upstream_summary`, and `upstream_sha1` fields. A conflict writes conflict markers and `merge.json`, then leaves `refs/base` and those YAML fields unchanged until the user resolves the file and runs `commit`.

`restore` discards local working-file edits by rewriting the local `.mw` file from cached `refs/base`. This is the mwsync equivalent of the common `git restore <path>` workflow, not a full reset of all local sync state. If `merge.json` exists, restore refuses unless `--abort-merge` is supplied; with that flag it restores `refs/base` content and clears the merge state. If a pending commit exists, restore leaves it in place unless `--discard-commit` is supplied.

`commit` resolves the article, reads the local file, obtains an edit summary from `-m/--message` or `$VISUAL`/`$EDITOR`, and writes a pending local edit to `_cache/<Article_Key>/commit.mw` plus `_cache/<Article_Key>/commit.json`. For existing pages, it records the current `refs/base` value as `base_revid` and refuses an unchanged file unless `--allow-empty` is used. If `merge.json` exists, `commit` refuses unresolved conflict markers and uses the merge target's upstream revid as `base_revid`, then clears `merge.json`. For new pages, `--new` records that the eventual edit should use MediaWiki `createonly` rather than `baserevid`. A second commit is refused unless `--amend` is used.

`push` resolves the article, reads the pending commit snapshot rather than the current working file, logs in with `MWSYNC_MW_USER` and `MWSYNC_MW_PASSWORD`, submits the edit, records push metadata, updates `refs/last-pushed`, clears the pending commit files, then re-fetches the page to resync the local file, cache, `refs/upstream`, and `refs/base`. With `--dry-run`, it reports the pending commit that would be pushed and does not contact the wiki.

`diff` compares cached revisions and local files using `git diff --no-index`. `diff New_York` compares `New_York@upstream` with the local working file. `diff New_York@upstream^ New_York@upstream` compares two cached revision expressions. With `--remote`, it first refreshes the upstream cache without rewriting the local working copy.

`difftool` launches `meld` against `New_York@upstream` and the local file.

`log` prints cached revision history from `history.jsonl`. If the earliest cached revision still points to a parent revision that is not present locally, `log` prints an incomplete-history note before the revision list.

`show` prints revision text for expressions such as `New_York@upstream`, `New_York@upstream^`, or `New_York@19778`. If metadata is known but the requested body is not cached yet, `show` fetches that one revision body by revid and stores it in the article cache.

`status` is a purely local mwsync-state command. It performs no network activity and does not inspect the surrounding Git repository. By default it prints a compact Git-like summary: articles with local `.mw` content that differs from cached `refs/base`, pending commits ready to push, unresolved merge state, missing local files, unfetched state, or fetched upstream revisions not yet merged. If all tracked articles are clean, it prints a single clean message. `status --verbose` preserves the detailed output with local path, URL, upstream revision metadata, refs, last pushed revision, pending commit, and merge state.

`fsck` checks cache consistency for one article or all registered articles. It reports legacy cache files, malformed refs, missing revision bodies or sidecars, non-chronological history entries, and ref/history mismatches. It does not repair files implicitly.

## Error Handling and Safety

The script is CLI-oriented: most user-facing failures print to stderr and call `sys.exit(1)`. This keeps command behavior predictable but means internal functions are not pure library APIs.

The main safety checks are:

- Subcommands other than `init` require an existing `mwsync.yaml`; they should
  not silently create a new working directory state.
- `fetch` does not overwrite local content; `merge` is responsible for changing the working `.mw` file.
- `commit` requires an upstream revision unless `--new` is specified.
- `push` requires a pending commit and uses its stored `base_revid` so
  MediaWiki can detect edit conflicts.
- Legacy `_cache/server--<Article_Key>.mw` files are detected and produce a clear migration/reset error.
- `show`, `diff`, and revision checkout fetch missing old revision bodies on demand when the history metadata identifies the requested revid.

## Transactional Writes

Commands that update multiple files should avoid visible partial state and
should clean up temporary artifacts on failure. The goal is not database-grade
rollback, but a simple rule: if a command reports failure, later commands
should not see a half-completed checkout, an unexpected new article entry, or a
ref that points to data that was not fully cached.

`fetch` should stage all new per-article cache state before updating live refs:

1. Fetch the current page body and requested metadata from the API.
2. Write new body and metadata files into a per-command staging directory, not
   directly into the live `_cache/<Article_Key>/` directory.
3. Build the new `history.jsonl` content in memory, including any metadata
   window requested by `--depth`, `--all-known`, or `--with-bodies`.
4. Validate that every ref target that will be written has a matching body and
   metadata sidecar where required.
5. Promote staged revid-named body and metadata files into the live cache with
   atomic renames. Existing matching immutable files may be reused.
6. Atomically replace `history.jsonl`.
7. Atomically update `refs/upstream` last.

If a failure occurs before the final ref update, the command should remove its
staging directory and must not update `history.jsonl` or `refs/upstream`.
Existing live cache files may remain, but a failed `fetch` should not introduce
new live cache files, refs, or manifests.

`checkout` should also stage work before committing user-visible state:

- It must require an existing `mwsync.yaml`.
- It may add the article entry to a staged config object, but should not save
  that config until the fetch and local-file write are ready to commit.
- It should avoid writing the local `.mw` file until the required upstream body
  and metadata are cached and validated.
- For a brand-new checkout, cache files and the local `.mw` content should be
  prepared in staging paths first. The final commit should promote staged cache
  files, write refs, write the local `.mw` file atomically, and save
  `mwsync.yaml`. If any final step fails, the command should attempt best-effort
  cleanup of files it created in that transaction and avoid claiming success.
- For an already-registered article, it should not rewrite registration
  metadata unless the operation completes.

`merge` may still write conflict markers into the local file on a real merge
conflict; that is an intentional user-visible result rather than a partial
failure. It should not advance `refs/base` or update `upstream_*` fields until
the merge succeeds cleanly.

`push` already depends on MediaWiki-side atomic edit semantics. Local
post-push resync should follow the same cache discipline as `fetch`: update
cache bodies and metadata first, then refs and config.

## External Dependencies

The script requires Python 3 and `PyYAML`. It shells out to `git` for modification checks and diffs, and to `meld` for visual diffs. Push operations require MediaWiki bot credentials in environment variables:

```bash
export MWSYNC_MW_USER='User@BotName'
export MWSYNC_MW_PASSWORD='bot-password'
```

No persistent cookies or tokens are stored by the script.
