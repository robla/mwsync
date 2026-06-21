# Git/SVN Subcommand Mapping and Command Design

This document compares the subcommand structures of Git, SVN, the legacy [mwsync.py](file:///home/robla/src/mwsync/mwsync.py) tool, and the proposed [mwmap.py](file:///home/robla/src/mwmap/mwmap.py) tool. It details the transition from the legacy, overloaded `checkout` verb to the cleaner `clone`, `restore`, and `switch` design.

## Overview: The Mapping Philosophy

The legacy [mwsync.py](file:///home/robla/src/mwsync/mwsync.py) combined configuration (which article maps to which file) and content synchronization (fetching, merging, pushing) into a single, somewhat opaque system. It adopted several Git-like verbs, but with significant semantic differences—most notably `checkout`.

The future [mwmap.py](file:///home/robla/src/mwmap/mwmap.py) architecture separates these responsibilities:
1. **Configuration & Pairing**: Explicitly adding sources and pairing specific remote locations (pages, subtrees, or entire wikis) to local paths.
2. **Data & Synchronization**: Moving wikitext between the remote wiki, the local cache, and the working directory.

By separating configuration from content synchronization, [mwmap.py](file:///home/robla/src/mwmap/mwmap.py) can adopt the cleaner UX conventions of modern Git (such as `restore` and `switch`) while deprecating overloaded legacy behaviors.

---

## Subcommand Comparison

| Action / Concept | Git Command | SVN Command | Legacy `mwsync.py` | Proposed `mwmap.py` | Notes / Alignment |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Initialize Workspace** | `git init` | *(N/A)* | `mwsync.py init` | `mwmap.py init` | Creates the metadata directory (`_mwmap/` or `_cache/`). |
| **Clone/Setup Workspace** | `git clone` | `svn checkout` | `mwsync.py checkout` | **`mwmap.py clone`** | Downloads remote repo/wiki content and initializes tracking configurations. |
| **Add Remote / Source** | `git remote add` | *(N/A)* | *(Implicit)* | `mwmap.py source add` | Registers a source (e.g. MediaWiki instance, local directory). |
| **Define Tracking / Mapping** | `git track` / `-u` | *(N/A)* | `mwsync.py add` | `mwmap.py pair <type>` | Links a remote wiki location (page, subtree, or wiki) to a local path. |
| **Fetch Remote to Cache** | `git fetch` | *(Implicit)* | `mwsync.py fetch` | `mwmap.py fetch` | Downloads remote revisions and metadata to local cache. |
| **Show Workspace Status** | `git status` | `svn status` | `mwsync.py status` | `mwmap.py status` | Compares working files, local cache/base, and remote upstream. |
| **Compare Changes** | `git diff` | `svn diff` | `mwsync.py diff` | `mwmap.py diff` | Performs line-by-line diffs. |
| **Snapshot Local Changes** | `git commit` | *(N/A)* | `mwsync.py commit` | *(Pending / TBD)* | Snapshots local edits as a pending change. |
| **Incorporate Changes** | `git merge` | `svn update` | `mwsync.py merge` | `mwmap.py merge` | Integrates fetched upstream revisions from cache into working files. |
| **Upload to Remote** | `git push` | `svn commit` | `mwsync.py push` | `mwmap.py push` | Uploads local edits or pending commits back to the wiki. |
| **Discard Local Changes** | `git restore` | `svn revert` | `mwsync.py restore` | **`mwmap.py restore`** | Overwrites working copy files with cached base versions. |
| **Switch Target/Branch** | `git switch` | `svn switch` | *(N/A)* | **`mwmap.py switch`** | Switches working copy between branches, profiles, or wikis. |
| **View History** | `git log` | `svn log` | `mwsync.py log` | `mwmap.py log` | Shows cached revision history logs. |
| **Show Specific Revision** | `git show` | `svn cat` | `mwsync.py show` | `mwmap.py show` | Displays the wikitext of a specific revision from cache. |
| **Integrity Check** | `git fsck` | *(N/A)* | `mwsync.py fsck` | `mwmap.py fsck` | Checks validity of caching structure and references. |
| **Legacy Setup / Track** | *(N/A)* | `svn checkout` | `mwsync.py checkout` | **`mwmap.py checkout`** | **Deprecated alias for `clone`**; prints warning. |

---

## Deep Dive: The Evolution of `checkout`, `clone`, `restore`, and `switch`

### The Problem with Legacy `checkout`

In `mwsync.py`, `checkout` acted as a "clone-and-track" helper command for an individual article. Under the hood, running `mwsync.py checkout <URL_OR_NAME>` registered the page in `mwsync.yaml`, fetched its history, and wrote it locally.

This is conceptually closer to an **`svn checkout <URL>`** (which pulls a specific remote subtree to a local path and tracks it) than it is to a **`git checkout`** (which historically did everything from switching branches to discarding file changes). 

Because `git checkout` was notoriously overloaded and had poor UX, Git eventually split its functionality in version 2.23:
* **`git switch`** is now used to change branches.
* **`git restore`** is now used to discard changes or restore files from older revisions.

### The `mwmap.py` Solution

To keep `mwmap.py` aligned with modern, clean version-control UX and avoid reproducing the confusing "brainspace" of the old `git checkout`, we will completely phase it out as an active sync/restore verb.

#### 1. `mwmap.py clone` (The New Entry Point)
`clone` becomes the official verb for initializing a remote wiki context or mapping and populating the workspace for the first time:
```sh
# Clone an entire remote config/wiki into a local directory
mwmap.py clone https://electowiki.org/w/ local_folder/
```

#### 2. `mwmap.py checkout` (The Deprecated Legacy Alias)
To accommodate external users of `mwsync.py` who are accustomed to `checkout` behaving like a single-page SVN-style checkout, we keep `checkout` as a deprecated synonym for `clone`:
```sh
# Deprecated SVN-style checkout for a single page
mwmap.py checkout electowiki:Maine --to Maine.mw
```
When run, this command will execute the clone/pair/fetch workflow but print a deprecation warning:
> `Warning: 'checkout' is deprecated and will be removed in a future version. Please use 'clone' or 'pair' instead.`

#### 3. `mwmap.py restore` (Discarding Changes)
To discard local modifications and revert files to their cached base state, use `restore` (matching `git restore` and the legacy [mwsync.py restore](file:///home/robla/src/mwsync/mwsync.py#L3507-L3515)):
```sh
# Revert a modified local file to its base version
mwmap.py restore Maine.mw
```

#### 4. `mwmap.py switch` (Switching Contexts)
If `mwmap` is extended to support multi-wiki synchronization, target profiles (e.g. staging vs. production), or branching, `switch` (matching `git switch`) will be used to swap the active working state:
```sh
# Switch the active target/profile of the working directory
mwmap.py switch staging
```
