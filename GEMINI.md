# mwsync Project Context

`mwsync.py` is a single-file Python 3 CLI tool designed to sync individual MediaWiki articles between a local `.mw` working copy and a remote wiki (default: [Electowiki](https://electowiki.org)). It follows a Git-like workflow with commands such as `fetch`, `merge`, `push`, `diff`, and `log`.

## Project Overview

- **Core Technology:** Python 3 (standard library + `PyYAML`).
- **Tools:**
    - `mwsync.py`: Main tool for syncing articles between local and MediaWiki.
    - `ledecopy.py`: Helper tool for importing English Wikipedia ledes into Electowiki drafts.
- **Architecture:** 
    - Single-file CLI scripts that share logic (ledecopy imports from mwsync).
    - Configuration stored in `mwsync.yaml`.
    - Per-article revision cache in `_cache/<Article_Key>/`.
    - Uses MediaWiki Action API (`w/api.php`).
- **Key Concepts:**
    - **Three Identities:** Article Key (canonical), Page Title (API), and Local Filename (usually `<Article_Key>.mw`).
    - **Cache Layout:** `history.jsonl` manifest, revid-named `.mw` bodies and `.json` sidecars, and state pointers in `refs/` (`upstream`, `base`, `last-pushed`).
    - **Atomic Operations:** Uses `_atomic_write()` (temp file + `os.replace`) for all config and cache updates.

## Directory Structure

- `mwsync.py`, `ledecopy.py`: Core CLI tools.
- `docs/`: Documentation including architecture specs, roadmap, and tool-specific docs.
- `cruft/`: Legacy or experimental notes (e.g., `roadmap-git.md`).
- `feb2026`: Symlink to a date-specific workspace/folder.
- `_cache/`: Local revision cache for synced articles (created at runtime).

## Building and Running

There is no build system. Run scripts directly with Python 3.

### mwsync.py
- **Help:** `python3 mwsync.py --help`
- **Tracked Articles:** `python3 mwsync.py status`
- **Sync Workflow:** `fetch` -> `merge` -> (edit) -> `push`.

### ledecopy.py
- **Usage:** `python3 ledecopy.py "Article Title"`
- **Workflow:** Imports lede from enwiki, prepares `.mw` file and `mwsync.yaml` entry for `push --new`.

### Validation
- **Syntax Check:** `python3 -m py_compile mwsync.py ledecopy.py`

### Credentials
Pushing requires MediaWiki bot credentials in environment variables:
```bash
export MWSYNC_MW_USER='User@BotName'
export MWSYNC_MW_PASSWORD='bot-password'
```

## Development Conventions

- **Code Style:** 
    - 4-space indentation.
    - `snake_case` for functions/variables, `UPPER_CASE` for constants.
    - Maintain minimal dependencies (Standard Library + `PyYAML`).
- **Error Handling:** CLI-style. Helpers often print to `stderr` and call `sys.exit(1)` on terminal errors.
- **Commits:** Short, imperative subjects with optional context after a dash (e.g., `Add fetch dry-run guard - preserve local edits`).
- **Safety:** `fetch` must never modify the local `.mw` file; only `merge` (and `checkout`) should perform writes to working copies.

## Testing

There is no automated test suite.
- **Validation:** Always run `python3 -m py_compile mwsync.py` and manually exercise affected subcommands (ideally with `--dry-run` if supported).
- **New Tests:** If added, place in a `tests/` directory with `test_*.py` filenames.

## Documentation References

- `docs/architecture-mwsync.md`: Detailed runtime model and API logic.
- `CLAUDE.md`: Quick reference for commands and architecture.
- `AGENTS.md`: Commit and coding style guidelines.
