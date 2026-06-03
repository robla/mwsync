# mwsync-ng: Migrating `mwmap` into the Next-Gen `mwsync`

Status: strategy / direction note, June 2026. Speculative parts are marked
*(tentative)*. This document lives in the `mwsync` repo but describes work that
mostly happens in the sibling `mwmap` repo (`/home/robla/src/mwmap`, reachable
through the untracked `mwmap` symlink). Do not edit `mwmap` through that symlink;
that repo has its own `AGENTS.md` and history.

## 1. What "next-gen" means here

`mwsync.py` works, but it is a ~3,600-line single-file monolith with one fixed
shape: **one local working directory ↔ one MediaWiki instance**, and within it a
**1:1 page ↔ `.mw` file** mapping. The sync engine (refs, three-way merge,
pending-commit / push, `history.jsonl` cache) is solid; the packaging and the
hard-coded cardinality are the limiting factors.

`mwmap` starts from a more general abstraction — a **map**: explicit rules
relating MediaWiki pages, page trees, namespaces, or whole wikis to local
structures (a `.mw` file, a folder tree, a Zim notebook, an Org file). The
1:1 page↔file case that `mwsync` hard-codes is just the simplest map.

The proposal in this document: treat `mwmap` as **mwsync 2.0**. Grow it until it
can express and execute the existing `mwsync` 1:1 workflow, port `mwsync`'s
proven sync engine into it as a reusable core, then retire `mwsync.py` (or reduce
it to a thin compatibility entry point). This is a *strangler-fig* migration, not
a rewrite-and-cut-over.

## 2. The two data models, side by side

| Concern | `mwsync.py` (today) | `mwmap` (prototype direction) |
| :--- | :--- | :--- |
| Scope per dir | One wiki, fixed | Many sources, explicit |
| Config file | `mwsync.yaml` (`wiki.api_base` + `wiki.articles`) | `_mwmap/config.yaml` (`sources: {}`, `mappings: []`) |
| Cardinality | 1:1 page ↔ `.mw` file only | page, subtree, namespace, whole-wiki |
| Remote model | MediaWiki only, hard-coded | typed sources (`mediawiki`, `zim`, …) |
| Cache | `_cache/<Article_Key>/…` | `_mwmap/cache/…` |
| Sync state | `refs/{upstream,base,last-pushed}`, `history.jsonl`, `commit.*`, `merge.json` | not yet designed — must be ported |
| Code shape | one `mwsync.py` | modular `commands/` + `core/` *(tentative)* |

The crucial observation: **`mwmap` has the better config/topology model, and
`mwsync` has the better sync engine.** The migration is mostly about moving the
sync engine under the more general topology, not inventing new sync mechanics.

## 3. Mapping `mwsync` onto `mwmap` concepts

A current `mwsync` working directory is equivalent to an `mwmap` project with:

- **One `mediawiki` source** — `wiki.api_base` becomes
  `sources.<name> = {type: mediawiki, location: <api_base>}`.
- **One implicit local source** — the working directory itself, a `localfiles`
  (or `mwfiles`) source rooted at `.`.
- **N `page` mappings** — each `wiki.articles.<Key>` entry becomes a
  `page` mapping from `mwwiki:<title>` to `local:<Key>.mw`, carrying the same
  per-article sync state (`upstream_revid`, refs, etc.).

So the 1:1 case is `mwmap` with one MediaWiki source, one local source, and a
list of `page` mappings. Nothing about the general model fights the old one; the
old one is a degenerate instance of it.

Two `mwmap` features that the old model gestured at but never generalized:

- **Namespaces.** `mwsync`'s namespace plan already uses `<NN>ns_<Name>/`
  local directories (e.g. `01ns_Talk/Software.mw`; see the project's namespace
  notes). In `mwmap` terms that is a `namespace` mapping from a MediaWiki
  namespace to a local folder — the same convention, made explicit instead of
  inferred.
- **Wiki-level caches.** `_cache/_categories/`, `_cache/_titles` etc. become
  source-scoped indexes under `_mwmap/cache/<source>/`.

## 4. Where the sync engine has to go

`mwmap`'s `docs/architecture.md` deliberately deferred a `refs/` store ("don't
inherit git-like expectations unless the storage model truly needs it"). For the
1:1 MediaWiki↔file map, **it truly needs it** — that is exactly the state
`mwsync` keeps to do safe three-way merges and conflict-detecting pushes. The
recommendation:

- Keep the existing per-pairing state model essentially as-is: a common
  ancestor (`base`), latest fetched (`upstream`), last pushed (`last-pushed`), a
  revision ledger, and pending `commit`/`merge` state.
- Store it *per mapping* under the disposable cache, e.g.
  `_mwmap/cache/<source>/<page-key>/{revisions/,refs/,history.jsonl,…}`.
  `mwmap`'s own doc already allows `_mwmap/revisions/` as the non-`refs`-flavored
  name if the git connotation is unwelcome.
- Treat this as the moment `mwmap` graduates from "local mapping metadata only"
  (its v0.01 target, which must *not* contact MediaWiki) to actually syncing.

This is the largest single piece of work and should be extracted from the
monolith rather than reimplemented (see §6).

## 5. Feature-parity matrix (the porting checklist)

Each `mwsync` verb needs an `mwmap` home before `mwsync.py` can be retired.
`mwmap`'s architecture note already anticipates some verb renames (`init` →
`mapinit`, `push` → `push --full`); the table picks concrete targets, all
*(tentative)*.

| `mwsync` | `mwmap` target | Notes |
| :--- | :--- | :--- |
| `init` | `mwmap init` | exists in v0.01 target |
| `add` / `checkout` | `mwmap pair page …` | registration + first fetch/merge |
| `fetch` | `mwmap fetch [mapping]` | cache + `upstream` only |
| `merge` | `mwmap merge` | three-way reconcile into local |
| `restore` | `mwmap restore` | discard local edits to `base` |
| `commit` | `mwmap commit -m` | stage pending edit |
| `push` | `mwmap push` | publish + re-fetch |
| `diff` / `difftool` | `mwmap diff` | `git diff --no-index`, `meld` |
| `log` / `show` | `mwmap log` / `show` | from the revision ledger |
| `status` | `mwmap status` | already in v0.01 target |
| `fsck` | `mwmap fsck` | cache/ref consistency |
| `migrate` | `mwmap migrate` | now *also* converts `mwsync.yaml`+`_cache` (see §7) |

Sister tools matter too: `ledecopy.py` imports `mwsync` private helpers
(`_parse_article_name`, `_atomic_write`, `_fetch_page`, `load_config`, …) by
design, and `catmgr.py` shares `_cache`. The port must either keep those import
surfaces working through a shim module or move those helpers into `mwmap.core`
and update the sister tools. Pick this deliberately — it is the main reason a
hard cut-over is risky.

## 6. Recommended sequencing (strangler-fig)

1. **Land `mwmap` v0.01 as specified** — `init` / `source add` / `status`,
   `_mwmap/config.yaml` + `_mwmap/cache/`, no network. Make the existing failing
   `tests/test_mwmap_cli.py` pass. This is pure local metadata and is the
   contract everything else builds on.
2. **Extract the sync engine from `mwsync.py` into an importable core**, *in the
   `mwsync` repo first* if that is lower-friction: pull the API layer
   (`_fetch_page`, login, CSRF, edit), the ref/`history.jsonl` cache, and the
   three-way merge / commit / push logic into modules with no `argparse` and no
   `sys.exit`. The monolith's CLI-terminal helpers (`load_config` et al. that
   print-and-exit) should grow library-friendly variants that *raise* instead.
   This is valuable even if `mwmap` stalls.
3. **Teach `mwmap` the `page` mapping over a `mediawiki` source**, backed by that
   extracted engine. Reach 1:1 parity with `mwsync` on a real Electowiki
   checkout. This is the milestone where `mwmap` becomes a credible `mwsync`
   replacement.
4. **Add the one-time `mwsync → mwmap` migration** (§7).
5. **Reduce `mwsync.py` to a deprecation shim** (or freeze it) once `mwmap`
   covers the daily workflow, then build out the genuinely new cardinalities
   (`subtree`, `namespace`, `wiki`) that justified `mwmap` in the first place.

Steps 1–2 are independent and can proceed in parallel; step 2's payoff (a
testable, non-monolithic engine) stands on its own merits regardless of `mwmap`.

## 7. User-facing migration: `mwsync.yaml` + `_cache` → `_mwmap`

Per the project's standing preference — *migrate, don't ship long-lived compat
shims* — this should be a one-shot converter, not a permanent dual-reader:

- A `migrate` command (in `mwmap`, or a `mwsync.py export-mwmap` helper) reads an
  existing `mwsync.yaml`, writes `_mwmap/config.yaml` with one `mediawiki`
  source, one local source, and one `page` mapping per article, and moves /
  re-lays-out `_cache/<Key>/` state under `_mwmap/cache/`.
- Follow the established migrate ergonomics: safe/lossless transforms run
  automatically; anything ambiguous prompts per entry; support `--dry-run` and
  `--yes`. `fsck` detects drift but does not silently rewrite.
- `mwmap`'s existing `docs/migration.md` currently frames the move as a
  structural `_cache` → `_mwmap` rename and leaves mappings as a *manual* step.
  Supersede that: the mapping rewrite should be automated, because the 1:1 case
  is mechanical.
- Settle the directory name (`_mwmap` vs `_mwsync`) **before** writing the
  converter, since the chosen name is baked into every migrated project. If
  `mwmap` is formally adopted as next-gen `mwsync`, `_mwsync` is the more honest
  name; `mwmap`'s own migration note already flags this rename as pending.

## 8. Open questions to resolve before committing

- **Project name & on-disk dir.** Is the shipped tool `mwmap`, `mwsync` 2.0, or
  both (one binary, two entry points)? What is the dir — `_mwmap` or `_mwsync`?
- **Repo topology.** `mwmap` is a separate git repo today. Does it stay separate,
  get merged into `mwsync`, or absorb `mwsync`? The sister-tool import coupling
  (§5) pushes toward a single repo eventually.
- **Multi-source in one project.** `mwsync` enforces one wiki per directory on
  purpose. `mwmap` allows many sources. Decide whether the 1:1 migration keeps
  the single-wiki guard or relaxes it from day one.
- **`refs/` vs `revisions/` naming** for the ported sync state (§4).
- **How much new cardinality is in scope for "next-gen v1"** versus parity-only
  first. Recommendation: ship parity first; defer `subtree`/`namespace`/`wiki`.

## 9. Smallest sensible next step

Make `mwmap` v0.01 pass its own tests (step 1) and, in parallel, start carving
the sync engine out of `mwsync.py` into raise-not-exit library functions
(step 2). Neither step requires resolving the naming/repo questions, and both are
useful even if the full merger never happens — which is exactly the property a
"reasonable migration strategy" should have.
