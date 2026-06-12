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
- **Run CLI (Target):** `python3 mwmap.py --help`
- **Initialize Workspace:** `python3 mwmap.py --root PATH init`
- **Run Tests:** `python3 -m pytest -q`
- **Search Code:** `rg <term>`
- **Check Status:** `git status --short`

## Development Conventions
Adhere to these guidelines when working on `mwmap`:

### Coding Style
- **YAGNI:** Prioritize simple solutions. Avoid over-engineering or premature abstraction.
- **Module Layout:** When implementation begins, follow a structure like `src/mwmap/` with subcommands in `commands/`.
- **CLI Design:** Use verb-style subcommands (e.g., `init`, `source`, `status`, `pair`, `fetch`, `push`).
- **Standardization:** No formatter or linter is currently configured. Follow existing Python patterns in the repo.

### Testing Practices
- **Existing Tests:** The tests in `tests/test_mwmap_cli.py` define the first milestone and are expected to fail until `mwmap.py` is implemented.
- **Policy:** Do not write tests concurrently with implementation unless explicitly requested.
- **Framework:** Use `pytest`. Name tests by behavior (e.g., `test_status_reports_unpaired_pages.py`).
- **Isolation:** Unit tests should cover pure logic without network access.

### Contribution Guidelines
- **Commits:** Use concise, sentence-style summaries (e.g., `Add initial mapping logic`).
- **Documentation:** Keep design notes in `docs/` and repository guidelines in `AGENTS.md`.
- **mwsync:** Do not edit the `mwsync` symlink.

## Key Directory Structure
- `_mwmap/`: (Planned) Local metadata storage.
    - `config.yaml`: Durable user-facing mapping configuration.
    - `cache/`: Disposable storage for remote-derived data.
- `docs/`: Architectural design and testing policy documents.
- `tests/`: Behavioral specs and CLI milestone tests.
- `mwmap.py`: (Planned) The main CLI entry point.

---
*This file was generated to provide context for AI-assisted development. Refer to `README.md` and `AGENTS.md` for more details.*
