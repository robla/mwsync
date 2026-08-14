# Repository Guidelines

## Project Structure & Module Organization
This repository combines the established `mwsync` CLI with its next-generation
replacement, currently developed under `mwmap/` on the `mwmap` branch.

- `mwsync.py`: main sync CLI for local `.mw` working files and MediaWiki pages.
- `mwmap/`: imported next-generation CLI, tests, design docs, and task roadmap.
- `ledecopy.py`: creates an mwsync-compatible Electowiki draft from an enwiki lede.
- `catmgr.py`: fetches and inspects the target wiki category cache.
- `docs/`: design notes and specs, including `architecture-mwsync.md`,
  `roadmap.md`, `legacy.md`, `ledecopy.md`, and `catmgr.md`.
- `cruft/`: old exploratory documents that are not current mainline guidance.

Runtime state is created in the working directory where the tools are run:

- `mwsync.yaml`: target wiki config and tracked article entries.
- `*.mw`: editable local article drafts or working copies.
- `_cache/<Article_Key>/`: per-article revision cache.
- `_cache/<Article_Key>/history.jsonl`: chronological revision manifest.
- `_cache/<Article_Key>/<revid>.mw` and `<revid>.json`: cached revision body and metadata.
- `_cache/<Article_Key>/refs/upstream`, `refs/base`, `refs/last-pushed`: sync refs.
- `_cache/<Article_Key>/commit.mw` and `commit.json`: pending local edit for `push`.
- `_cache/<Article_Key>/merge.json`: unresolved merge-conflict state.
- `_cache/categories/`: planned category cache for `catmgr.py`.
- `_mwmap/mwmap.yaml` and `_mwmap/cache/`: next-generation config and cache.

The legacy `_cache/server--<Article_Key>.mw` format is intentionally not mainline
state. Current code should detect it and produce a friendly migration/reset
error, not silently read it.

## Build, Test, and Development Commands
Use Python 3 directly; there is no build system.

- `python3 -m py_compile mwsync.py ledecopy.py`: syntax check both scripts.
- `python3 mwmap/mwmap.py --help`: show next-generation subcommands.
- `pytest -q tests`: run the green legacy suite.
- `pytest -q mwmap/tests`: run next-generation specifications.
- `pytest -q`: collect both suites from the combined root.
- `ruff check --no-cache .`: run the enforced Python lint baseline.
- `python3 mwsync.py --help`: show mwsync subcommands.
- `python3 ledecopy.py --help`: show ledecopy usage.
- `python3 catmgr.py --help`: show category cache commands.
- `python3 mwsync.py init`: create a minimal `mwsync.yaml`.
- `python3 mwsync.py add Maine`: register an article by page name.
- `python3 mwsync.py checkout Maine`: register, fetch, and merge into `Maine.mw`.
- `python3 mwsync.py fetch Maine`: update `_cache` and `refs/upstream` only.
- `python3 mwsync.py merge Maine`: update the local `.mw` from fetched upstream.
- `python3 mwsync.py restore Maine`: discard local `.mw` edits and restore `refs/base`.
- `python3 mwsync.py commit Maine -m "Update Maine"`: snapshot local edits as a pending wiki edit.
- `python3 mwsync.py push Maine`: publish the pending commit to the wiki.
- `python3 mwsync.py diff Maine@upstream^ Maine@upstream`: compare cached revisions.
- `python3 mwsync.py fsck`: check cache refs, history, and revision files.
- `python3 ledecopy.py "New York"`: create a new Electowiki draft from the enwiki lede.
- `python3 catmgr.py fetch`: refresh `_cache/categories/` from the configured target wiki.

`fetch` is intentionally git-like: it does not rewrite the local working file.
Use `merge` or `checkout` when the local `.mw` should change.

## Coding Style & Naming Conventions
Follow the existing Python style: 4-space indentation, `snake_case` for functions
and variables, `UPPER_CASE` for constants, and concise docstrings for non-trivial
helpers. Keep dependencies light; the code relies on the standard library plus
`PyYAML`.

Prefer small helpers with clear side effects and direct stderr messages for CLI
failures. Article keys and default local filenames use `Article_Title` and
`Article_Title.mw` normalization.

Use the MediaWiki Action API (`w/api.php`) for current scripts. Set a
User-Agent on HTTP requests.

## Testing Guidelines
The repository has separate legacy and next-generation pytest suites. During
the `t0002` TDD phase, the combined command intentionally reports 11 failures;
do not hide them by running only root `tests/`. For changes, run the affected
suite and report the known baseline separately from new failures.

Do not write tests concurrently with implementation unless explicitly
requested. If existing tests are insufficient for a safe change, stop and ask
before editing. Every new test must begin with a comment under 500 characters
describing what it hopes to accomplish.

At minimum also run:

- `python3 -m py_compile mwsync.py ledecopy.py`
- `python3 mwsync.py --help`
- `python3 ledecopy.py --help`
- affected subcommand smoke tests, preferably in a temp directory

Use mocked/local smoke tests for network-sensitive behavior when possible. If
live Electowiki/enwiki behavior matters, say whether you did or did not run a
live network test.

Place legacy tests under `tests/` and next-generation tests under
`mwmap/tests/`. Name files `test_<feature>.py`.

Ruff lint is enforced. Ruff formatting is selected but not yet enforced because
the initial format pass changes many legacy and imported files; make that a
dedicated mechanical commit. Static typing is deferred until the scripts have a
useful annotation baseline rather than introducing a permanently ignored check.

## Combined-Repository Transition

Treat the `mwmap` branch in this repository as the development authority. Do
not accumulate new implementation commits in the standalone `mwmap` checkout.
Keep legacy `mwsync.py` and its state formats usable until the roadmap explicitly
cuts over command names. The locked roadmap is `mwmap/tasks.org`.

## Commit & Pull Request Guidelines
Use short, imperative commit subjects, optionally with context after a dash, for
example:

```text
Align fetch with git-style cache refs
Add ledecopy draft generator
```

Pull requests should explain the user-facing workflow change, list verification
commands, and call out MediaWiki API, config, cache, credential, or migration
effects.

## Security & Configuration Tips
Never commit real bot passwords, session cookies, or personal wiki credentials.
Push uses:

```bash
export MWSYNC_MW_USER='User@BotName'
export MWSYNC_MW_PASSWORD='bot-password'
```

Keep credential exports in an untracked local shell file.

## Environment Notes
The user works in Crostini Debian on a Chromebook. Bubblewrap is version 0.8.0 by
default there. Do not ask the user to upgrade Bubblewrap just to complete routine
repo work. If sandboxed file creation fails because of the known apply-patch or
bwrap behavior, use the approved shell fallback only when the user asks for it,
and document the limitation in the final response.
