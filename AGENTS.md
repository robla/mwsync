# Repository Guidelines

## Project Structure & Module Organization

This repository is a prototype-stage design repository for `mwmap`. `README.md` defines the concept, and `docs/` contains focused architecture and testing notes. The `mwsync` path is an untracked symlink to the sibling `mwsync` project and should not be treated as part of this repository unless explicitly added later.

Planned Python source layout, when implementation begins, is expected to resemble:

```text
src/
  mwmap/
    cli.py
    commands/
    core/
```

Keep the structure modest. Add modules only when there is working code that needs them.

## Build, Test, and Development Commands

There is no build system, package metadata, or test suite in this repository yet.

- `git status --short`: check local changes before editing.
- `git log --oneline`: review the short commit-history style.
- `rg <term>`: search repository text quickly.

When Python packaging is introduced, document the exact commands here before relying on them.

## Coding Style & Naming Conventions

No formatter or linter is configured yet. For future Python code, prefer clear module names, small command modules, and explicit names that match planned CLI verbs, such as `commands/init.py`, `commands/status.py`, or `commands/pair.py`. Avoid a single large `mwmap.py` monolith unless the implementation remains trivial.

Use Markdown headings consistently in design documents. Keep prose direct and mark speculative architecture as tentative.

## Testing Guidelines

No tests exist yet. Do not write tests concurrently with implementation unless explicitly requested. If existing tests are missing, incomplete, or not targeted enough to make a requested change confidently and safely, stop and ask before editing.

Prefer `pytest` if a test suite is introduced later, unless the project adopts a different framework. Name tests by behavior, for example `test_status_reports_unpaired_pages.py`. Document any fixtures that require MediaWiki credentials, local notebooks, or external services.

## Commit & Pull Request Guidelines

Recent commits use concise, sentence-style summaries, for example `Clarify relationship between mwsync and mwmap`. Follow that style: describe the change plainly without prefixes unless a convention is later adopted.

Pull requests should include a short purpose statement, a summary of changed files, and any assumptions about the evolving relationship between `mwmap` and `mwsync`. Link related issues or design notes when available. Include command output for tests once tests exist.

## Agent-Specific Instructions

Do not edit through the untracked `mwsync` symlink. Keep contributor guidance aligned with the prototype status and avoid inventing commands, package names, or workflows not present in the repo.
