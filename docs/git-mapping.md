# Git Subcommand Mapping and Command Design

This document compares the subcommand structures of Git, the legacy `mwsync.py`
tool, and the proposed `mwmap.py` tool. It explains which `mwmap` verbs map
directly onto Git, where `mwmap` deliberately introduces a new verb (`pair`),
and why the overloaded `checkout` verb is deprecated from the start in favor of
`restore` and `switch`.

> **Naming note:** `mwmap.py` is intended to become the next generation of
> `mwsync.py`, most likely via a straight rename (`mwmap.py` → `mwsync.py`) with
> verbs preserved. Read every `mwmap.py <verb>` below as something that should
> also work as `mwsync.py <verb>`. When that rename happens, this document — and
> its "legacy `mwsync.py`" vs. "proposed `mwmap.py`" columns — will be rewritten.

## Design principle

`mwmap` follows three rules when choosing verbs:

1. **Match Git where the concept matches.** If an operation is the same one Git
   performs (fetch, status, diff, merge, push, log, show, restore), use Git's
   name and semantics.
2. **Introduce a new verb only where Git lacks the concept.** `pair` is the main
   case — see below.
3. **Don't inherit Git's mistakes.** Git's `checkout` was overloaded and was
   split in Git 2.23 into `switch` and `restore`. `mwmap` starts from that
   cleaner split rather than reproducing the old behavior.

## Overview: configuration vs. synchronization

The legacy `mwsync.py` combined configuration (which article maps to which file)
and content synchronization (fetching, merging, pushing) into a single, somewhat
opaque system. It adopted several Git-like verbs but with significant semantic
differences — most notably `checkout`.

`mwmap.py` separates these responsibilities:

1. **Configuration & pairing** — registering remotes and pairing specific
   locations (pages, subtrees, namespaces, or entire wikis) to local paths.
2. **Data & synchronization** — moving wikitext between the remote wiki, the
   local cache (`_mwmap/cache/`), and the working directory.

This separation lets `mwmap` adopt modern Git's clean verbs (`restore`,
`switch`) while leaving overloaded legacy behavior behind.

---

## Subcommand comparison

| Action / Concept | Git Command | Legacy `mwsync.py` | Proposed `mwmap.py` | Notes / Alignment |
| :--- | :--- | :--- | :--- | :--- |
| **Initialize workspace** | `git init` | `mwsync.py init` | `mwmap.py init` | Creates the `_mwmap/` metadata directory. |
| **Register a remote** | `git remote add` | *(implicit / hardcoded)* | `mwmap.py remote add` | Registers a remote to sync against (e.g. a MediaWiki instance); its location may itself be local. |
| **Pair remote ↔ local** | *(no single verb; cf. `git branch -u`)* | `mwsync.py add` / `checkout` | **`mwmap.py pair <type>`** | Links a page, subtree, namespace, or wiki to a local path. New verb — see below. |
| **Clone (onboard in one step)** | `git clone` | `mwsync.py checkout` | **`mwmap.py clone`** *(first version)* | One-command onboarding of a page, subtree, or wiki: `init` (if needed) + `remote add` + `pair` + `fetch` + populate. Direct successor to `mwsync.py checkout`. |
| **Fetch remote → cache** | `git fetch` | `mwsync.py fetch` | `mwmap.py fetch` | Downloads remote revisions/metadata to cache; no working-tree changes. |
| **Show workspace status** | `git status` | `mwsync.py status` | `mwmap.py status` | Compares working files, cached base, and remote upstream. |
| **Compare changes** | `git diff` | `mwsync.py diff` | `mwmap.py diff` | Line-by-line diffs across working / base / upstream. |
| **Snapshot local changes** | `git commit` | `mwsync.py commit` | *(TBD)* | `mwmap` may use automatic diffing instead of an explicit commit step. |
| **Integrate upstream** | `git merge` (cf. `git pull`) | `mwsync.py merge` | `mwmap.py merge` | Integrates fetched upstream revisions from cache into working files. |
| **Upload to remote** | `git push` | `mwsync.py push` | `mwmap.py push` | Uploads local edits back to the wiki. |
| **Discard / populate working files** | `git restore` | `mwsync.py restore` | **`mwmap.py restore`** | Rewrites working files from the cached base, including files that are missing. |
| **Switch context** | `git switch` | *(n/a)* | `mwmap.py switch` *(future)* | Switches the active profile, target wiki, or branch. |
| **View history** | `git log` | `mwsync.py log` | `mwmap.py log` | Shows cached revision history. |
| **Show a revision** | `git show` | `mwsync.py show` | `mwmap.py show` | Displays the wikitext of a cached revision. |
| **Integrity check** | `git fsck` | `mwsync.py fsck` | `mwmap.py fsck` | Checks validity of the cache and recorded revision state. |
| **Legacy setup (deprecated)** | `git checkout` | `mwsync.py checkout` | `mwmap.py checkout` *(deprecated)* | Deprecated shim; warns and points to `pair`/`clone`/`restore`. |

---

## The case for `pair` as a new verb

Most `mwmap` verbs map one-to-one onto Git because the underlying operation is
identical. `pair` is the exception: it names something Git has no clean verb for.

Git's tracking relationship (`git branch --set-upstream-to`, abbreviated `-u`)
links a local branch to a remote branch *within a single content model* — both
sides are Git refs. `mwmap`'s `pair` links a remote wiki location (page,
subtree, namespace, or whole wiki) to a local path *across two different content
models* (wikitext ⇄ Zim / Org-mode / Markdown). Git expresses its version of
this through a combination of remote-tracking refs, refspecs, and upstream
config; there is no single user-facing verb for it — and there is no
`git track` command.

Because the "map" between systems is the entire reason `mwmap` exists, that
relationship deserves a first-class, readable verb:

```sh
mwmap.py pair page    electowiki:ElectoramaNews  ElectoramaNews.mw
mwmap.py pair subtree electowiki:Category:Maine  maine/
mwmap.py pair wiki    electowiki                 .
```

The local side of each pairing is just a path: the directory where you run
`mwmap` (its `--root`) is the implicit local working tree, never registered or
named — exactly as Git never makes you name your working tree. The named stores
you pair *against* are remotes (see "Register a remote" above).

Its inverse is `unpair`. This is a deliberate, justified departure from Git —
adding a concept Git lacks — as distinct from arbitrarily renaming a concept
Git already has.

---

## The evolution of `checkout` → `clone`, `restore`, and `switch`

### Why legacy `checkout` is a poor fit

In `mwsync.py`, `checkout` acted as a "clone-and-track" helper for a single
article. Running `mwsync.py checkout <URL_OR_NAME>` did three things at once:

1. **Registered** the page mapping in `mwsync.yaml` (akin to `pair`).
2. **Fetched** the revision history into the local cache (akin to `fetch`).
3. **Populated** the local working file with content from the cache.

This mirrors Git's own history: `git checkout` grew to do too much — switching
branches *and* discarding file changes *and* populating the working tree. In
Git 2.23 the command was split for clarity:

* **`git switch`** changes branches / context.
* **`git restore`** discards changes or restores files from a revision.

### How `mwmap` handles it

`mwmap` starts after that lesson and adopts the clean split immediately, so it
never carries `checkout`'s ambiguity:

#### `mwmap.py restore` — discard or populate working files

Rewrites working files from the cached base. This covers both discarding local
edits and writing files that are missing (e.g. right after a `pair` + `fetch`):

```sh
# Revert a modified local file to its base version
mwmap.py restore maine/Elections_2026.mw
```

#### `mwmap.py clone` — onboard in one step (first version)

`clone` is the primary onboarding verb and a near-term priority, because the
common workflow is to *start* a session by pulling one page down to edit —
today's `mwsync.py checkout https://electowiki.org/wiki/California`. `mwmap.py
clone` is that command with a cleaner name: given a page URL (or a subtree or a
whole-wiki location), it runs `init` (if needed), registers the remote, pairs the
location, fetches, and writes the local file — matching `git clone` as the
one-command path from nothing to a working copy.

```sh
# Onboard a single page (the common case)
mwmap.py clone https://electowiki.org/wiki/California

# Or a whole wiki into the current working tree
mwmap.py clone https://electowiki.org/w/ .
```

#### `mwmap.py switch` — change context (future)

Once `mwmap` supports multiple contexts — target profiles (staging vs.
production), multiple wikis, or local branches — `switch` changes the active one,
matching `git switch`:

```sh
mwmap.py switch staging
```

#### `mwmap.py checkout` — deprecated shim

To ease migration for users with `mwsync.py` muscle memory, `checkout` is
retained only as a deprecated alias for the legacy single-page setup workflow
(`pair` + `fetch` + populate). When invoked it performs that workflow and warns:

> `Warning: 'checkout' is deprecated. Use 'clone' to onboard a page or wiki, 'pair' for finer-grained setup, and 'restore' to discard local changes.`

It is not a recommended verb and may be removed in a future version.

### Recommendation

- Use **`clone`** to onboard a page, subtree, or wiki in one step (the direct successor to `mwsync.py checkout`), or **`pair`** for finer-grained mapping setup.
- Use **`restore`** to discard local changes or populate working files from cache.
- Reserve **`switch`** for switching between contexts once they exist.
- Treat **`checkout`** as deprecated from the start.
