# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

`mwmap` is a **prototype/design-stage** CLI for keeping MediaWiki content paired
with other local wiki-like formats (Zim notebooks, Org-mode files, Markdown
trees). The core abstraction is a **map**: rules describing how wiki objects
(page, subtree, namespace, whole wiki) correspond to local structures, with
two-way sync as the long-term goal.

This directory is the `mwmap/` subtree of the combined `mwsync` repository,
imported non-destructively onto the `mwmap` branch (not yet merged to `main`;
see `docs/repository-merge.md` for the import record). It is the intended
next-generation replacement for the repo-root legacy `mwsync.py` — read the
repo-root `CLAUDE.md` for that implementation, and the repo-root
`docs/coexistence.md` for the rules when both tools manage the same working
files during the transition. `tasks.org` here is the locked roadmap for that
transition.

`src/mwmap/` is the current CLI implementation. The root `mwmap.py` is a thin
source-checkout entry point (adds `src/` to `sys.path`, calls `mwmap.cli.main()`)
that the tests hardcode as `PROJECT_ROOT / "mwmap.py"`; the `mwmap` shell
wrapper calls `python3 mwmap.py`. Locally, `mwm` is often used as a shorthand
alias for `mwmap.py` in examples and conversation — the two names are
interchangeable. This subdirectory also holds design docs (`README.md`,
`docs/`), contributor guidance (`AGENTS.md`, `GEMINI.md`), a roadmap
(`tasks.org`, task IDs locked as of 2026-08-13), and a pytest suite. Much of
`git log` is still design history rather than implementation history.

## Commands

- `python3 -m pytest -q` — run the full test suite (fast, offline; see Testing below).
- `python3 -m pytest -q tests/test_mediawiki.py::test_name` — run a single test.
- `python3 mwmap.py --help` — show the current CLI surface.
- `python3 mwmap.py init && python3 mwmap.py clone https://electowiki.org/wiki/California` — smoke-test the first networked clone workflow.
- `rg <term>` — search repository text.

There is no build system, package metadata, formatter, or linter configured yet.

## Command surface

Verbs mirror Git where the concept matches (see `docs/git-mapping.md` for the
full comparison table and rationale). Every command takes a global `--root PATH`
(default: current directory).

- `init` — creates `_mwmap/mwmap.yaml` and `_mwmap/cache/`.
- `remote add NAME TYPE LOCATION` — registers a remote (e.g. a MediaWiki instance).
- `clone URL [PATH] [--follow]` — onboards a MediaWiki page URL end to end: inits if
  needed, registers a remote from the URL, pairs, fetches, writes the local file.
  Onboards a redirect page itself (and warns) unless `--follow` is passed.
- `fetch [PATH]` — downloads the latest upstream revision (by stable pageid) into
  the cache; touches neither the working tree nor `base_revid` (like `git fetch`).
- `merge [PATH]` — three-way merges cached upstream into working files against
  `base_revid`. Clean merge advances `base_revid`; conflicts write
  `<<<<<<< / ======= / >>>>>>>` markers, leave `base_revid` unchanged, and exit
  nonzero. Refuses a file with unresolved markers. Pure-Python, no `git` binary.
- `pull [PATH]` — `fetch` then `merge`.
- `commit [PATH] [-m MSG] [--amend] [--allow-empty]` — stages a pending edit
  (body + summary + the `base_revid` it was based on) per page; refuses files
  with unresolved conflict markers or no-op changes.
- `preview [PATH] [--output PATH] [--open] [--link]` — renders a pending commit
  (preferred) or working file through the remote MediaWiki parser; can reconcile
  a compatible manual browser save back into local state. See `docs/preview.md`.
- `push [PATH] [--dry-run]` — publishes staged commits to MediaWiki, guarded by
  the staged `base_revid` (an upstream change since that base is rejected as an
  edit conflict — resolve with `pull`, re-`commit`, retry). On success the new
  revision is re-cached, `base_revid` advances, the pending commit clears.
  Credentials come only from `MWMAP_MW_USER` / `MWMAP_MW_PASSWORD` env vars (a
  MediaWiki bot password), never from config. Push/login/CSRF code is adapted
  from legacy `mwsync.py` (see `docs/legacy-code-copy.md`).
- `status` — reports configured remotes and the mapping count.
- `fsck` — checks cache/mapping integrity (partial writes from a crash mid-fetch,
  drift between cache and durable config).
- `migrate [PATH] [--all]` — upgrades legacy single-upstream page mappings to the
  multi-upstream schema (see below). Bare `migrate` refuses to guess when more
  than one legacy mapping remains.

Commands needing config must exit nonzero with a clear message before `init`.
See `tests/test_mwmap_cli.py` for exact expected output strings on the
init/remote/status path, and README.md for the full worked command list.

## Architecture and metadata model

- `_mwmap/mwmap.yaml` is **durable, user-facing** state: remote definitions and
  mapping rules. `_mwmap/config.yaml` was the prototype name and is still read
  as a legacy fallback, but new workspaces write `mwmap.yaml` — this rename is
  settled, don't revert it. `_mwmap/cache/` is **disposable** — anything
  repopulatable from remotes. Do not create `_mwmap/refs/` (it implies a
  git-like ref store mwmap shouldn't inherit); if revision storage is needed,
  prefer `_mwmap/revisions/`.
- The local working tree (`--root`) is never registered or named, mirroring
  Git's asymmetric model — everything synced against is a registered **remote**
  under `remotes:` in `mwmap.yaml`.
- Page cache is keyed by stable MediaWiki **pageid**, not movable title, so a
  page move/rename doesn't orphan history:
  ```
  _mwmap/cache/<remote>/
    site.yaml                    # siteinfo: server, paths, namespace table
    pages/<pageid>/
      page.yaml                  # current title/namespace/base+current revid
      history.jsonl              # per-revision ledger
      <revid>.mw / <revid>.yaml  # cached body + metadata sidecar per revision
      <title-key>.mw -> <revid>.mw   # readable alias to the base-revision body
    by-title/<NNns_Name>/<title-key> -> ../../pages/<pageid>   # readable index
  ```
  `fsck` checks for partial per-page writes (a crash mid-fetch) rather than
  relying on locking. Full rationale and examples: `docs/architecture.md`.
- **Multi-upstream direction** (`docs/multi-upstream.md`): one local file will
  track more than one remote page (e.g. an Electowiki article and a Crostini
  mirror), moving `base_revid` from the mapping level down to per-upstream
  `upstreams:` entries keyed by remote name. There is deliberately **no
  load-bearing `version:` field** — legacy mappings are recognized by top-level
  `remote`/`pageid`/`base_revid`; multi-upstream mappings by an `upstreams:`
  map. `migrate` rewrites old shapes to the new one; ordinary reads don't
  silently upgrade. `tasks.org` tracks the in-flight task breakdown for this.
- Source layout and call stack:
  ```
  mwmap.py -> mwmap.cli.main() -> mwmap.commands.<verb>.run_*(args)
            -> mwmap.workspace / mwmap.sync / mwmap.core helpers
  ```
  `workspace.py` owns pairing/config and cache-path/layout helpers (load/save
  `mwmap.yaml`, page cache read/write, aliasing). `sync.py` owns content
  movement between remote, cache, and working tree (fetch/push composition) —
  this is the deliberate split between **pairing/config** and **content sync**.
  `core/remote.py` defines the `Remote` protocol (the single seam a backend
  plugs into — `MediaWikiRemote` today; commands and `sync.py` never talk to a
  backend directly) and `core/mediawiki.py` holds the raw MediaWiki API calls.
  `core/textmerge.py` has the three-way merge; `core/misc.py` has small shared
  helpers (`atomic_write_text`, `die`).
- Subcommands are **verb-style**, deliberately aligned with Git semantics.
  `docs/git-mapping.md` maps Git ↔ legacy `mwsync.py` ↔ `mwmap` verbs and
  explains why `pair` is a new verb and why `checkout` is split into
  `clone`/`restore`/`switch` from the start. `docs/architecture.md` is the
  source of truth for direction over this file.
- Verbs may eventually need to become `mwsync` verbs (e.g. `mwmap init` →
  `mwsync mapinit`). Choose names with that migration path in mind — `mwmap`
  may become "mwsync 2.0", a plugin to it, or an `mwsync` rearchitecture; the
  leading expectation is a straight rename (`mwmap.py` → `mwsync.py`) with
  verbs preserved, so each verb should read naturally as `mwsync.py <verb>`.

## Working conventions specific to this repo

- **YAGNI is a stated rule.** Modules under `src/mwmap/{cli,commands/,core/}`
  should stay small and exist only when working code needs them.
- Each function should have a brief docstring. Use short call-stack notes in
  modules that coordinate multiple layers; avoid large explanatory comments.
- **Do not write tests concurrently with implementation unless explicitly
  asked.** If existing tests are missing, incomplete, or not targeted enough to
  make a change safely, stop and ask first. This matters most for sync, merge,
  page-identity, and revision-state behavior. Each test starts with a short
  (<500 char) comment stating its intent. See `docs/testing.md`.
- Tests mix subprocess CLI tests (`tests/test_mwmap_cli.py`, spawning
  `mwmap.py`) with in-process unit tests that import `mwmap.*` directly
  (`tests/conftest.py` puts `src/` on `sys.path`). `tests/conftest.py` also
  installs an autouse fixture that blocks real socket connections — MediaWiki
  behavior is tested by stubbing `urlopen` with canned JSON (see
  `test_mediawiki.py`), never by hitting a live wiki, so the suite stays fast
  and offline. That fixture only covers the in-process tests, not the spawned
  CLI subprocess.
- **Legacy `mwsync.py` now lives at the combined repo root** (`../mwsync.py`
  from here, a ~3,600-line monolith) — a real tracked file in this same repo,
  not a gitignored symlink to a separate checkout. `docs/legacy-code-copy.md`
  still says what's safe to port verbatim (MediaWiki-facing logic, merge
  algorithms) versus what must be re-homed (anything touching page identity,
  config shape, or storage — mwmap uses stable pageids and `mwmap.yaml`, not
  mwsync's title-derived keys and `refs/base`/`refs/upstream` files). It's
  editable in-repo now (e.g. `tasks.org` task `t0004.4` depends on that), but
  people coexist on its current behavior — see the repo-root `CLAUDE.md` and
  `docs/coexistence.md` before changing it, and don't rename or remove it
  outside the `t0007` naming cutover.
- Commit messages: concise, sentence-style summaries, no prefixes (e.g.
  "Clarify relationship between mwsync and mwmap"). Recent multi-model commits
  append an attribution suffix like `(Claude)` or `(Gemini)`.

## Multi-LLM collaboration

This repo is edited by several models (ChatGPT, Gemini, Claude). `docs/llm-log.org`
is a shared work log. **After a substantive change, add your own one-line entry**
in its Org format:

```
** Claude [YYYY-MM-DD Ddd HH:MM]: short description of the substantive change
```

One entry per substantive edit (skip trivial churn). Log only your *own* work —
do not write entries on another model's behalf.
