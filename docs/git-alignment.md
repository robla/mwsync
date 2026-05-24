# Git Alignment and Differences

`mwsync.py` is designed with a Git-like command structure and mental model to make syncing MediaWiki pages feel familiar to developers. However, because it syncs local files with a live wiki rather than a Git remote, there are several key differences in workflows, architecture, and assumptions that can trip up users expecting identical behavior.

---

## Git Alignment

`mwsync.py` intentionally mirrors several core Git concepts and subcommands:

- **Reference Model:** It tracks version state using reference pointers stored under `_cache/<Article_Key>/refs/`.
  - `refs/upstream` tracks the last known revision ID fetched from the wiki (equivalent to a remote tracking branch like `refs/remotes/origin/main`).
  - `refs/base` tracks the common ancestor revision ID of the local file and the remote wiki (equivalent to the merge base).
  - `refs/last-pushed` tracks the revision ID of the last successful push from the local environment.
- **Subcommands:**
  - **`init`**: Bootstraps the local workspace config (`mwsync.yaml`), similar to `git init`.
  - **`fetch`**: Downloads the latest page content and revision metadata from the remote wiki into the local cache (`_cache/`) and updates `refs/upstream` without modifying the local working `.mw` files.
  - **`merge`**: Reconciles local changes with fetched remote changes using a three-way merge. Under the hood, `mwsync.py` invokes `git merge-file` to perform this merge using `refs/base` as the common ancestor.
  - **`diff` / `difftool`**: Compares differences between the local working file, cached revisions, or different refs (e.g. `mwsync.py diff Article@upstream^ Article@upstream`), calling `git diff --no-index` or `meld` under the hood.
  - **`log`**: Prints a chronological history list of cached revision metadata from the manifest (`history.jsonl`), similar to `git log`.
  - **`show`**: Prints the text body of a specific cached revision (e.g. `Article@12345` or `Article@upstream`), similar to `git show`.

---

## Key Differences

Users accustomed to Git should be aware of the following differences:

### 1. `push` Combines Commit and Push (FIXME)
In Git, saving a local snapshot (`git commit`) and sending changes to the server (`git push`) are separate, explicit steps. This allows developers to commit frequently, work offline, and review commits before sharing them.
- **In `mwsync.py`**: The `push` command performs both steps at once. It reads the local file, prompts the user for an edit summary (if `-m` is not provided), logs into the wiki, uploads the revision, and immediately refetches the new revision metadata.
- > [!NOTE]
  > **FIXME:** In the near term, we should separate the "commit" and "push" phases to allow offline history tracking, local version checkpoints, and pre-push review.

### 2. Verbose `status` Output (FIXME)
In Git, `git status` is a lightweight command that concisely lists modified, staged, or untracked files.
- **In `mwsync.py`**: The `status` subcommand is highly verbose. It lists all registered articles and prints detailed configurations for each, including local path, URL, current upstream revision, and all raw reference paths (`refs/upstream`, `refs/base`, `refs/last-pushed`).
- > [!NOTE]
  > **FIXME:** The current verbose output should be gated behind a `--verbose` (or `-v`) flag. By default, `mwsync.py status` should provide a clean, concise summary of modified or out-of-sync articles.

### 3. `checkout` Behaves Differently
In Git, `git checkout <branch>` switches branches, or `git checkout <file>` discards unstaged changes.
- **In `mwsync.py`**: `checkout` acts as a multi-step setup command for new pages. Running `mwsync.py checkout <Page_Title>` performs `add` (tracks the page in `mwsync.yaml`), `fetch` (caches the upstream page history), and `merge` (writes the text to a local working `.mw` file) all in one go. It interacts with the network, unlike Git checkouts which are purely local.

### 4. `add` Registers/Tracks files, it does not "Stage" them
In Git, `git add` stages modified files to the index (staging area) in preparation for a commit.
- **In `mwsync.py`**: `add` is used to register a new MediaWiki page title or URL in the `mwsync.yaml` config so that `mwsync` knows to track it. There is no concept of a staging area; any modification to a tracked `.mw` file is sent directly on `push`.

### 5. MediaWiki Revision IDs vs. Git Commit Hashes
- **Git** tracks commits as a Directed Acyclic Graph (DAG), identified by cryptographic hashes (SHA-1/SHA-256), allowing branching, merging, and distributed collaboration.
- **MediaWiki** tracks history as a strictly linear sequence of revisions per-page, identified by database-generated incremental integer revision IDs (`revid`). Branching is not natively supported on a per-page level.

### 6. Scoped to Individual Pages (No Repository-Wide Operations)
- **Git** operates repository-wide. A single commit tracks changes across multiple directories and files simultaneously, capturing the repository's state as a unified snapshot.
- **`mwsync.py`** is designed to synchronize individual pages. Commands like `fetch`, `merge`, and `push` target a single article key at a time. It does not support transactionally pushing changes to multiple pages in a single revision.

### 7. Environment Variables for Authentication
- **Git** integrates with SSH keys, OS credential managers, or helper utilities to authenticate.
- **`mwsync.py`** requires the credentials for the remote wiki bot password to be set as environment variables (`MWSYNC_MW_USER` and `MWSYNC_MW_PASSWORD`) in the running shell.
