# Git Alignment and Differences

`mwsync.py` borrows Git's command vocabulary where that helps explain the
workflow: fetch remote state, merge it into a working file, review diffs, then
push a new revision. It is not a Git wrapper or a Git-backed store. It syncs
MediaWiki pages, whose identity, history, authentication, and merge model differ
from Git in ways that can surprise Git power users.

## What Is Git-Like

- `mwsync.py init` creates the local workspace config, roughly like `git init`.
- `mwsync.py fetch ARTICLE` downloads remote page state into `_cache/` and
  updates `_cache/<Article_Key>/refs/upstream` without rewriting the local
  working `.mw` file.
- `mwsync.py merge ARTICLE` performs a three-way merge from cached upstream into
  the working file, using `_cache/<Article_Key>/refs/base` as the merge base.
- `mwsync.py diff`, `difftool`, `log`, and `show` support Git-like review of
  local files, cached revisions, and refs such as `Article@upstream`.
- `refs/upstream`, `refs/base`, and `refs/last-pushed` intentionally resemble
  Git refs, but they point at MediaWiki revision IDs rather than Git objects.
- The implementation uses Git tools for local mechanics where practical,
  including `git diff --no-index` and `git merge-file`.
- Historical builds also used `git status --porcelain` as part of mwsync status
  reporting. That was convenient for one Git-tracked mirror workflow, but it
  conflates mwsync sync state with the user's surrounding Git repository.

## Differences Likely To Surprise Git Users

### 1. `commit` and `push` Are Split, But Still Page-Scoped

Like Git, `mwsync.py commit` creates a local snapshot and `mwsync.py push`
publishes an already-created snapshot. Unlike Git, the pending commit is one
page at a time and is stored as readable files under
`_cache/<Article_Key>/commit.json` and `commit.mw`, not as part of a repository
commit graph.

### 2. `status` Is Concise By Default

Git's default `status` is compact and centered on what changed. `mwsync.py
status` now follows that model: default output lists only articles needing
attention, while `mwsync.py status --verbose` reports registered articles, local
paths, URLs, upstream metadata, and raw cache refs. The status model should be
based on mwsync state: working files compared to `refs/base`, pending commits,
merge state, and fetched-but-unmerged upstream revisions. Whether those files
are also dirty in the surrounding Git repository is a separate concern.

### 3. There Is No Staging Index

`git add` stages content into the index. `mwsync.py add` registers a MediaWiki
page in `mwsync.yaml`; it does not snapshot file content. A modified tracked
`.mw` file is the candidate content for `commit`.

### 4. There Is No Local Commit Graph

Git stores an immutable object graph of commits, trees, and blobs. `mwsync.py`
stores readable MediaWiki revision bodies and metadata under `_cache/`, plus at
most one pending commit per article. Rich local draft history is normally
preserved by the surrounding Git repository, not by an internal mwsync commit
database.

### 5. Surrounding Git State Is Useful But Not Canonical

Using a normal Git repository around `mwsync.yaml`, `.mw` files, and selected
docs can be very useful for local review, personal history, and backups. That
Git repository should not define mwsync cleanliness, though. A future helper
should audit alignment between mwsync state and the user's Git working tree
without folding Git dirtiness into core sync commands.

### 6. `checkout` Means Page Materialization

`git checkout` or `git switch` changes branches, while `git checkout <path>` can
restore a path. `mwsync.py checkout ARTICLE` is closer to a convenience setup
flow: register the article if needed, fetch it, and merge/materialize the local
working `.mw` file. It can use the network and can create local files.

### 7. `restore` Is The Working-File Discard Command

`mwsync.py restore ARTICLE` is intentionally close to the common
`git restore <path>` use: discard local working-file edits and restore the file
from the current base revision. It is not `git reset`; pending commits remain
unless `--discard-commit` is supplied, and merge state is cleared only with
`--abort-merge`.

### 8. There Is No `pull` Command Yet

Git users may expect `pull` to mean `fetch` followed by `merge`. `mwsync.py`
currently exposes those as separate commands and has not committed to a `pull`
shortcut.

### 9. No Branches, `HEAD`, or Current Remote Branch

Revision expressions use page-scoped names such as `Article@upstream`,
`Article@base`, `Article@last-pushed`, `Article@12345`, and parent syntax such
as `Article@upstream^`. There is no repository-wide `HEAD`, no branch checkout,
and no equivalent of `origin/main`.

### 10. Commands Are Page-Scoped

Git commands usually operate on a repository snapshot. `mwsync.py fetch`,
`merge`, `commit`, `log`, `show`, and `push` operate on one article at a time.
There is no single transaction that pushes a coherent multi-page change set to
the wiki.

### 11. Some Read-Looking Commands May Use the Network

Most Git read commands are local unless they explicitly contact a remote.
`mwsync.py fetch` and `push` obviously use the MediaWiki API, but other commands
can also be less purely local than Git users expect. For example, `show` may
fetch a missing cached revision body, and `diff --remote` refreshes upstream
before diffing.

### 12. `diff --remote` Mutates the Cache

Plain Git `diff` does not update remote-tracking refs. In `mwsync.py`,
`diff --remote` is intentionally convenience-oriented: it fetches current remote
state, updates cache refs, then compares. Use explicit cached revision
expressions when a strictly local comparison is desired.

### 13. `log` Is Cached, Per-Article, and May Be Incomplete

`mwsync.py log ARTICLE` is reverse chronological like `git log`, but it reports
only the cached MediaWiki history window for that article. If older revisions
have not been fetched, the output should make the missing parent boundary
visible.

### 14. `mwsync.yaml` Is Durable Sync State

Git keeps most ref state inside `.git/`. `mwsync.py` deliberately stores tracked
article entries and selected upstream metadata in `mwsync.yaml` so that useful
mirror state can live in normal version control alongside `.mw` files.

### 15. Article Identity Is Not Just a File Path

MediaWiki has page titles, namespace IDs, dbkeys, URLs, local filenames, and
revision IDs. `mwsync.py` must map between them. Main-namespace pages usually
look like `Article_Title.mw`; non-main namespaces use local namespace
directories such as `01ns_Talk/Software.mw` or
`02ns_User/RobLa__Journal.mw`. The article key in `mwsync.yaml` remains the
stable lookup handle.

### 16. One Workspace Targets One Wiki

Git repositories can have multiple remotes. An mwsync workspace is intentionally
dedicated to one MediaWiki instance via the global `wiki.api_base` in
`mwsync.yaml`. Checking out a URL from another wiki should fail clearly rather
than silently reinterpret the title against the configured wiki.

### 17. MediaWiki Revisions Are Mostly Immutable, Not Git Objects

Git objects are content-addressed and immutable. MediaWiki revisions have stable
integer IDs, but visibility, metadata, and availability can change through wiki
administration actions such as deletion or suppression. The cache is optimized
for mostly immutable revisions, not guaranteed immutable objects.

### 18. Merge Conflicts Have No Index or Abort Machinery

`mwsync.py merge` uses `git merge-file`, so conflict markers are familiar. It
also writes a small `merge.json` state file so `commit` can finish the merge
against the fetched upstream revid. But there is no Git index with staged
`ours/base/theirs` entries, no branch state, and no `git merge --abort`
equivalent. Resolving a conflict means editing the working `.mw` file and then
running `mwsync.py commit`.

### 19. Auth Uses MediaWiki Bot Credentials

Git authentication commonly uses SSH keys, credential helpers, or host-specific
token storage. `mwsync.py push` currently expects MediaWiki bot credentials in
environment variables such as `MWSYNC_MW_USER` and `MWSYNC_MW_PASSWORD`.

### 20. `fsck` and `migrate` Are Project Maintenance Commands

Git users may recognize the names, but the scope is different. `mwsync.py fsck`
checks consistency among `mwsync.yaml`, readable cache files, refs, and revision
manifests. `mwsync.py migrate` is for old mwsync cache/layout formats, not for
Git repository history.
