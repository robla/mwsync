# Git Subcommand Mapping and Command Design

This document compares the subcommand structures of Git, the legacy [mwsync.py](file:///home/robla/src/mwsync/mwsync.py) tool, and the proposed [mwmap.py](file:///home/robla/src/mwmap/mwmap.py) tool. It details how [mwmap.py](file:///home/robla/src/mwmap/mwmap.py) can align closer to Git's CLI model and makes the case for how the `checkout` verb should behave.

## Overview: The Mapping Philosophy

The legacy [mwsync.py](file:///home/robla/src/mwsync/mwsync.py) combined configuration (which article maps to which file) and content synchronization (fetching, merging, pushing) into a single, somewhat opaque system. It adopted several Git-like verbs, but with significant semantic differences—most notably `checkout`.

The future [mwmap.py](file:///home/robla/src/mwmap/mwmap.py) architecture separates these responsibilities:
1. **Configuration & Pairing**: Explicitly adding sources and pairing specific remote locations (pages, subtrees, or entire wikis) to local paths.
2. **Data & Synchronization**: Moving wikitext between the wiki, the local cache, and the working directory.

By separating configuration from content synchronization, [mwmap.py](file:///home/robla/src/mwmap/mwmap.py) can map more cleanly to Git's design.

---

## Subcommand Comparison

| Action / Concept | Git Command | Legacy `mwsync.py` | Proposed `mwmap.py` | Notes / Git Alignment |
| :--- | :--- | :--- | :--- | :--- |
| **Initialize Workspace** | `git init` | `mwsync.py init` | `mwmap.py init` | Creates the metadata directory (`_mwmap/` or `_cache/`). |
| **Add Remote / Source** | `git remote add` | *(Implicit / hardcoded)* | `mwmap.py source add` | Registers a source (e.g. MediaWiki instance, local directory). |
| **Define Tracking / Mapping** | `git track` / `git branch -u` | `mwsync.py add` / `checkout` | `mwmap.py pair <type>` | Links a remote wiki location (page, subtree, or wiki) to a local directory or file. |
| **Fetch Remote Data to Cache** | `git fetch` | `mwsync.py fetch` | `mwmap.py fetch` | Downloads remote revisions and metadata to local cache without modifying working files. |
| **Show Workspace Status** | `git status` | `mwsync.py status` | `mwmap.py status` | Compares working files, local cache/base, and remote upstream. |
| **Compare Changes** | `git diff` | `mwsync.py diff` | `mwmap.py diff` | Performs line-by-line diffs between working copy, base, and upstream. |
| **Snapshot Local Changes** | `git commit` | `mwsync.py commit` | *(Pending / TBD)* | `mwsync` snapshots edits to cache as a "pending wiki edit". `mwmap` may use automatic diffing or explicit commits. |
| **Incorporate Cached Changes** | `git merge` | `mwsync.py merge` | `mwmap.py merge` | Integrates fetched upstream revisions from cache into working files. |
| **Upload to Remote** | `git push` | `mwsync.py push` | `mwmap.py push` | Uploads local edits or pending commits back to the wiki. |
| **Discard Local Changes** | `git restore` | `mwsync.py restore` | `mwmap.py restore` | Overwrites working copy files with cached base versions. |
| **View History** | `git log` | `mwsync.py log` | `mwmap.py log` | Shows cached revision history logs for tracked entities. |
| **Show Specific Revision** | `git show` | `mwsync.py show` | `mwmap.py show` | Displays the wikitext of a specific revision from cache. |
| **Workspace Integrity Check** | `git fsck` | `mwsync.py fsck` | `mwmap.py fsck` | Checks validity of caching structure and references. |
| **Populate Working Copy** | `git checkout` / `git switch` | `mwsync.py checkout` | **`mwmap.py checkout`** | *See deep dive below.* |

---

## Deep Dive: The Case for `checkout` in `mwmap.py`

### How `checkout` Behaved in Legacy `mwsync.py`

In `mwsync.py`, `checkout` acted as a "clone-and-track" helper command for an individual article. Under the hood, running `mwsync.py checkout <URL_OR_NAME>` performed three operations in sequence:
1. **Registered** the page mapping in `mwsync.yaml` (akin to `git add`/`pair`).
2. **Fetched** the revision history from the wiki to the local `_cache` (akin to `git fetch`).
3. **Populated** the local working file (e.g. `Maine.mw`) with the content from the cache (akin to `git checkout`).

Because `mwsync.py` lacked separate subcommand primitives for defining sources and page pairs, it overloaded `checkout` to serve as the initial workspace entry point for new pages. This is a departure from Git, where `git checkout` assumes the repository and tracked paths are already configured and fetched.

### The Case for `checkout` in `mwmap.py` (Git Alignment)

In `mwmap.py`, we can align `checkout` with Git's actual semantics: **populating and switching the working tree based on existing repository state and mappings**.

Here is how `checkout` fits into the decoupled workflow:

#### 1. Decoupled Setup and Checkout (Populating the Working Tree)
When setting up mappings for the first time (especially with multi-page maps or subtree maps), pairing does not immediately populate the filesystem. 
```sh
# 1. Register sources and mappings
mwmap.py source add electowiki mediawiki https://electowiki.org/w/
mwmap.py pair subtree electowiki:Category:Maine_Elections local:maine/

# 2. Fetch the metadata and content into _mwmap/cache/
mwmap.py fetch

# 3. Populate the working directory based on the fetched cache
mwmap.py checkout
```
In this scenario, `mwmap.py checkout` reads the active mappings and the cached remote versions, then creates the local folder tree and `.mw` files in the working directory. It behaves like `git checkout` by updating the working directory to match the target index/cache.

#### 2. Discarding Working Tree Changes
In Git, `git checkout <path>` (or the newer `git restore <path>`) is used to discard uncommitted changes in the working directory by copying the file content from the index/HEAD.

While `mwmap.py` has `restore` (which maps to `git restore`), `checkout` is a highly familiar alias for this action:
```sh
# Discard local changes to a file and revert it to the last synced base
mwmap.py checkout maine/Elections_2026.mw
```

#### 3. Switching Mapped Configurations or Target Branches (Future Extension)
If `mwmap` is extended to support multi-wiki synchronization, target profiles, or local branches, `checkout` can switch the active mapping context or target:
```sh
# Switch the active target/profile of the working directory
mwmap.py checkout staging
mwmap.py checkout production
```

### Recommendation

We should retain the `checkout` subcommand in `mwmap.py`, but redefine it to align with Git:
- **Do not** use `checkout` to register/pair new resources (use `mwmap.py pair` for that).
- **Use `checkout`** to:
  1. Populate missing local files from the fetched cache (e.g. after adding a new pair or fetching a new wiki-mapping).
  2. Discard local modifications to working files by restoring them from the cache base (as an alias or extension of `restore`).
  3. (Optional future) Switch active profile configurations or branch states.
