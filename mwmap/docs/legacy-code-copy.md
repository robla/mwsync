# Copying code from legacy `mwsync.py`

`mwmap` is the next generation of `mwsync.py`, and the legacy tool already
implements much of what `mwmap` is growing into — MediaWiki fetch, three-way
merge, login/CSRF/edit (push), namespace handling, and title normalization.
Before re-deriving any of that, check whether it can be **copied and adapted**
instead. This document says what to copy, what to leave behind, and why.

> **Source access.** The legacy code lives at the gitignored `mwsync` symlink
> (`/home/robla/src/mwsync/mwsync.py`, a ~3,600-line monolith). It is **not part
> of this repo — read it for reference, never edit through the symlink.** Line
> numbers below are against that file as of this writing; treat them as hints,
> not contracts.

## The one rule that governs everything

**Copy algorithms and MediaWiki-facing logic; re-home anything that touches
identity, config shape, or storage.**

`mwsync` and `mwmap` made three deliberately different foundational choices:

| Concern | legacy `mwsync` | `mwmap` |
| :-- | :-- | :-- |
| Page identity | title-derived **key** | stable **pageid** |
| Revision pointers | `refs/base`, `refs/upstream` files (`_read_ref`/`_write_ref`) | `base_revid` in `mwmap.yaml` (+ `page.yaml`) |
| Config | single hardcoded `wiki:` + `articles:` | many `remotes:` + `mappings:` |
| Cache key | `_cache_dir(key)` (title) | `cache/<remote>/pages/<pageid>/` |

So a function that *talks to MediaWiki* or *merges text* is a clean copy
candidate. A function that *resolves an article key*, *reads a ref file*, or
*assumes one wiki* must be rewritten against `mwmap`'s pageid + multi-remote
model — copying it wholesale would re-introduce the page-move orphaning and
git-like `refs/` store that `mwmap` exists to avoid.

## High-value copy candidates

These are well-tested, fiddly, or security-sensitive — reinventing them is the
wrong kind of work.

- **Push: `_mw_login`, `_mw_get_csrf_token`, `_mw_edit_page`** (mwsync ~1239–1330).
  ~80 lines implementing the login-token → login → csrf-token → edit dance,
  including `baserevid` **edit-conflict detection** (the `editconflict` API
  error). `mwmap` has no push yet; copy these into `core/mediawiki.py` and
  expose a `MediaWikiRemote.push_page(title, text, baserevid, summary)`.
  Adapt: `api_base` → `remote.api_url`; drop the `key` parameter.
  **Credentials:** mwsync reads `MWSYNC_MW_USER` / `MWSYNC_MW_PASSWORD` from the
  environment (bot password), *not* from config — keep that pattern (secrets
  out of `mwmap.yaml`); rename the vars to `MWMAP_MW_USER` / `MWMAP_MW_PASSWORD`.
  **User-Agent Policy:** MediaWiki APIs require descriptive user agents to prevent
  throttling/blocking. Maintain compliance with the legacy pattern:
  `USER_AGENT = "mwsync/1.0 (+https://electowiki.org/)"` (adapted for `mwmap`).

- **`_edit_summary`** (mwsync ~1332). Opens `$VISUAL`/`$EDITOR` for a commit
  summary with a stripped `#` comment block; empty summary aborts. Copy when
  adding `commit`/`push`.

- **`_fetch_revision_by_revid`** (mwsync ~802). Fetches a specific revid's body.
  `mwmap` lacks this, and `merge` currently *dies* if the base body is missing
  from cache. Copy/adapt so merge can re-fetch the base instead.

- **`_has_conflict_markers`** (mwsync ~2109). Copy the predicate **exactly** —
  mwmap's current version only matches `<<<<<<< ` and misses `=======` and
  `>>>>>>> `. (Aligned in this change; see below.)

- **`_run_merge_file`** (mwsync ~1842). A 10-line `git merge-file -p` wrapper.
  Relevant only as the alternative to mwmap's pure-Python merge — see the
  trade-off note below.

- **Transient HTTP Preview Server: `_serve_preview_document`, `_open_transient_preview`, `_close_preview_server`** (mwsync ~1133–1212).
  Enables a secure, loopback-only HTTP server on `127.0.0.1` that hosts rendered
  wikitext previews and automatically triggers the default browser using the `webbrowser`
  module. Utilizes Content Security Policy (CSP) header pinning to secure local scripts.
  Crucial for bypassing browser CORS/file-access constraints during offline previews.

- **Namespace / title normalization** (`_normalize_namespace_map` ~192,
  `_fetch_namespace_map` ~259, `_namespace_local_dir` ~170, `_encode_dbkey_segment`
  ~166, `_parse_title_parts` ~336, `_canonical_title` ~601, `_normalize_dbkey`
  ~152). These encode hard-won MediaWiki edge cases (dbkey casing, namespace
  prefixes, underscores↔spaces). Copy the **logic** for robustness, but feed it
  into `mwmap`'s pageid-keyed dirs and siteinfo-driven namespace map — do not
  copy the surrounding key/dir layout.
  **Done (working filenames):** `workspace.local_path_for_page` now mirrors
  mwsync's `_local_for_title_parts` / `_namespace_local_dir` / `_encode_dbkey_segment`
  — main-namespace pages stay flat (`<dbkey>.mw`), other namespaces go under a
  `NNns_Name/` directory (`02ns_User/RobLa.mw`), and subpage slashes become `__`.
  This replaced an earlier `local_path_for_title` that left a literal `:` in
  working filenames (broke `rsync`/`scp` `host:path` parsing, Windows, and Zim's
  `:` page separator). One deliberate deviation from mwsync: a stray `:` in a
  *main-namespace* title is also escaped to `__` (mwsync leaves it).

- **Bulk Namespace Title Indexing (`wikimgr.py`)**. Uses the MediaWiki `allpages`
  API to query and cache page lists and stable `pageid` numbers for entire namespaces.
  This is a critical reference pattern for implementing `mwmap.py` subtree and
  whole-wiki tracking maps.

- **Recent Changes Cache (`rcmgr.py`)**. Caches recent changes logs (`YYYY-MM-DD.jsonl`)
  to support delta-syncing and changes detection on the wiki without querying every page.

- **Fetch transaction / history integrity** (`_cache_fetch_transaction`,
  `_history_content`, `_cache_revision_metadata`; mwsync ~1516–1720). The
  current `mwmap` cache writer is simpler and adequate for v1, but mwsync's
  transaction logic is useful when adding depth windows, metadata-only history,
  or all-known revision fetches. Copy the atomicity and duplicate/history
  semantics; adapt paths to `cache/<remote>/pages/<pageid>/` and keep
  `base_revid` in config.

- **Pending commit reconciliation & Lost-Edit Recovery** (`run_commit`,
  `_reconcile_saved_pending`, `_record_saved_revision`, `_reconcile_saved_preview_proposal`;
  mwsync ~2320–2580). Stages pending edits (`commit.mw` and `commit.json`) and detects
  when a parent-compatible version of our pending edit has already landed upstream.
  This reconciles the repository and prevents duplicate submissions in case of network
  dropouts right after a write. Reuse the workflow concepts, re-homed to the pageid-keyed cache.

- **Wikipedia Importer & Category Resolver (`ledecopy.py`, `catmgr.py`)**.
  Fetches Wikipedia article ledes, handles interactive category mapping prompts, registers
  mappings to `catmap.yaml`, and prepares draft templates. This workflow is a high-value
  candidate for an **extension or plugin** for `mwmap.py`, rather than being built
  into the core tool.

## Already re-derived or intentionally changed

Some `mwmap` code was written before this inspection and turns out to match
`mwsync`'s proven logic. Other parts are intentionally smaller for the current
prototype.

- **Merge control flow.** `mwmap`'s `commands/merge.py::_merge_one` matches the
  important mapped-page cases in `mwsync`'s `run_merge` (mwsync ~2715):
  populate-if-missing → up-to-date/local-matches-upstream → fast-forward →
  3-way → conflict. It is not a full clone of legacy behavior: mwsync also has
  base-less adoption, trailing-final-newline normalization, saved merge-state
  files, and `_ensure_cached_body` recovery for missing upstream bodies. Keep
  mwmap's smaller ladder for now, but treat those mwsync cases as known
  follow-up decisions rather than assuming they were intentionally rejected.
- **API fetch.** `fetch_mediawiki_page` / `fetch_mediawiki_page_by_id` mirror
  `_fetch_page` (mwsync ~716); mwmap additionally fetches by **pageid** and
  returns a redirect note, which mwsync does not.
- **Atomic write.** `core.misc.atomic_write_text` ≈ mwsync `_atomic_write` (~1217).

### Trade-off: pure-Python merge vs `git merge-file`

`mwsync` shells out to `git merge-file`; `mwmap` ships a dependency-free
`core/textmerge.py`. Output was verified identical on the cases tried, including
that adjacent disjoint edits are a genuine diff3 conflict. The choice:

- **Pure-Python (current):** self-contained — a wiki-sync tool shouldn't require
  the `git` binary just to merge text. More code to own.
- **`git merge-file` (mwsync):** ~10 lines, battle-tested, standard `-L` labels.
  Adds an external runtime dependency.

`mwmap` prefers self-containment, so the pure-Python merge stays — but
`_run_merge_file` is trivially copyable if that preference changes.

## Do **not** copy wholesale

- **Key/title-based cache and ref files** — `_cache_dir`, `_ref_path`,
  `_read_ref`/`_write_ref`, `_history_path(key)` (mwsync ~1362–1462). These key
  everything on a title-derived string and store revision pointers as files
  under a `refs/`-style tree — exactly the two things `mwmap` deliberately
  rejected (pageid identity; `base_revid` in config; no `_mwmap/refs/`). Reuse
  the *semantics* (base vs upstream vs merge-state), not the storage.
- **Config/entry resolution** — `load_config`, `find_article_entry`,
  `resolve_article_entry` (mwsync ~104, ~514, ~674). Bound to the single-wiki
  `wiki:`/`articles:` shape; `mwmap` resolves `(remote, pageid)` across many
  remotes instead.
- **The monolith shape itself.** `mwsync.py` is one 3,600-line file; `mwmap`'s
  reason to exist includes *not* repeating that. Copy functions into the small
  `core/`, `commands/`, `sync.py`, `workspace.py` modules, not into one blob.

## Immediate concrete actions

1. **Done in this change:** align `_has_conflict_markers` in
   `commands/merge.py` with mwsync's stronger predicate (catch `=======` and
   `>>>>>>> ` too).
2. **Done (push):** copied `_mw_login` / `_mw_get_csrf_token` / `_mw_edit_page`
   into `core/mediawiki.py` as `mediawiki_login` / `mediawiki_csrf_token` /
   `mediawiki_edit_page` (edit by stable pageid; `MediaWikiEditConflict` raised
   on `editconflict`); `MediaWikiRemote` gained `login()` + `push_page()`; the
   `push` command reads creds from `MWMAP_MW_USER` / `MWMAP_MW_PASSWORD` and
   tells the user to `pull` then retry on conflict.
   Also adopted mwsync's **`commit` → `push` two-step**: `commit` stages a
   pending edit (`commit.mw` + `commit.json`) in the page's cache dir
   (adapting `_write_pending_commit` / `_pending_commit` / `_clear_pending_commit`,
   re-homed from key-based `_cache/<key>/` to `cache/<remote>/pages/<pageid>/`),
   and `push` submits the staged commit rather than the working tree. `_edit_summary`
   was copied as `commit._prompt_summary`. **Not** copied: mwsync's reconciliation
   (`_reconcile_saved_pending`, `last-pushed` ref) and `--new` page creation; a
   pending commit is also not auto-invalidated by `merge`/`pull` (re-`commit`).
3. **When `merge` must tolerate a missing base body:** copy
   `_fetch_revision_by_revid` so it re-fetches rather than dying. Also decide
   whether to copy mwsync's trailing-final-newline normalization.
4. **When adding subtree/namespace mappings:** copy the namespace-normalization
   helpers, adapted to siteinfo + pageid dirs.
5. **When adding history depth or commit/push:** revisit the transaction and
   pending-commit sections above before inventing new storage behavior.
