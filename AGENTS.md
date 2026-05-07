# Repository Guidelines

## Project Structure & Module Organization
This repository is a small Python CLI toolkit for Electowiki/MediaWiki workflows.

- `mwsync.py`: main sync CLI for local `.mw` working files and MediaWiki pages.
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
- `_cache/categories/`: planned category cache for `catmgr.py`.

The legacy `_cache/server--<Article_Key>.mw` format is intentionally not mainline
state. Current code should detect it and produce a friendly migration/reset
error, not silently read it.

## Build, Test, and Development Commands
Use Python 3 directly; there is no build system.

- `python3 -m py_compile mwsync.py ledecopy.py`: syntax check both scripts.
- `python3 mwsync.py --help`: show mwsync subcommands.
- `python3 ledecopy.py --help`: show ledecopy usage.
- `python3 catmgr.py --help`: show category cache commands.
- `python3 mwsync.py init`: create a minimal `mwsync.yaml`.
- `python3 mwsync.py add Maine`: register an article by page name.
- `python3 mwsync.py checkout Maine`: register, fetch, and merge into `Maine.mw`.
- `python3 mwsync.py fetch Maine`: update `_cache` and `refs/upstream` only.
- `python3 mwsync.py merge Maine`: update the local `.mw` from fetched upstream.
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
There is no committed automated test suite yet. For changes, at minimum run:

- `python3 -m py_compile mwsync.py ledecopy.py`
- `python3 mwsync.py --help`
- `python3 ledecopy.py --help`
- affected subcommand smoke tests, preferably in a temp directory

Use mocked/local smoke tests for network-sensitive behavior when possible. If
live Electowiki/enwiki behavior matters, say whether you did or did not run a
live network test.

If tests are added later, place them under `tests/` and name files
`test_<feature>.py`.

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
