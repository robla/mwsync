# GEMINI.md - mwmap Project Context

## Project Overview
`mwmap` is an experimental Python command-line tool designed to maintain mappings between MediaWiki content (pages, trees, namespaces, or entire wikis) and local wiki-like formats such as Zim desktop wiki notebooks, Org-mode files, or Markdown folder trees. It aims to support ongoing two-way synchronization while preserving page identity, links, and structure.

Currently, the project is in a **prototype/idea stage**. It is a companion or potential successor to the sibling `mwsync` project, exploring a more flexible mapping architecture.

### Tech Stack
- **Language:** Python 3
- **Testing:** pytest
- **Configuration:** YAML (PyYAML)

## Building and Running
The project does not yet have a build system or package metadata (YAGNI).

### Key Commands
- **Run CLI:** `python3 mwmap.py --help`
- **Initialize Workspace:** `python3 mwmap.py --root PATH init`
- **Migrate Mappings:** `python3 mwmap.py migrate --all`
- **Run Tests:** `python3 -m pytest -q`
- **Search Code:** `rg <term>`
- **Check Status:** `git status --short`

## Development Conventions
Adhere to these guidelines when working on `mwmap`:

### Coding Style
- **YAGNI:** Prioritize simple solutions. Avoid over-engineering or premature abstraction.
- **Module Layout:** The implementation lives in `src/mwmap/`; root `mwmap.py` is a thin source-checkout entry point.
- **CLI Design:** Use verb-style subcommands (`init`, `remote`, `clone`, `fetch`, `merge`, `pull`, `commit`, `preview`, `push`, `status`, `fsck`, `migrate`). Keep command block registration alphabetical.
- **Standardization:** No formatter or linter is currently configured. Follow existing Python patterns in the repo.
- **Documentation:** Give each function a brief docstring. Use short call-stack notes in coordinating modules, not large explanatory comments.

### Testing Practices
- **Existing Tests:** The tests cover both CLI subprocess behavior and in-process logic (fetch, merge, commit, push, fsck, and migration).
- **Policy:** Do not write tests concurrently with implementation unless explicitly requested.
- **Framework:** Use `pytest`. Name tests by behavior (e.g., `test_status_reports_unpaired_pages.py`).
- **Intent Comments:** Every test should start with a short comment, under 500 characters, describing what it hopes to accomplish.
- **Isolation:** Unit tests should cover pure logic without network access (stubbed via `urlopen` with canned JSON).
- **Integration:** Live-network behaviors belong in separate, opt-in integration tests, not the fast local suite.

### Contribution Guidelines
- **Commits:** Use concise, sentence-style summaries (e.g., `Add initial mapping logic`). Recent commits append an attribution suffix (e.g., `(Gemini)` or `(Claude)`).
- **Documentation:** Keep design notes in `docs/` and repository guidelines in `AGENTS.md` and `CLAUDE.md`.
- **mwsync:** Do not edit the `mwsync` symlink.

## Key Directory Structure
- `_mwmap/`: Local metadata storage created in workspaces.
    - `mwmap.yaml`: Durable user-facing mapping configuration (formerly `config.yaml`, which is still supported as a fallback but not written).
    - `cache/`: Disposable storage for remote-derived data, keyed by stable numeric page ID.
- `src/mwmap/`: Python implementation package.
    - `cli.py`: argument parsing and dispatch.
    - `commands/`: verb handlers.
    - `workspace.py`: workspace config/cache helpers.
    - `core/`: low-level MediaWiki, remote adapters, and text merge helpers.
- `docs/`: Architectural design and testing policy documents.
- `tests/`: Behavioral specs and CLI milestone tests.
- `mwmap.py`: Thin source-checkout CLI entry point.

---
*This file was generated to provide context for AI-assisted development. Refer to `README.md` and `AGENTS.md` for more details.*
