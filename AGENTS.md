# Repository Guidelines

## Project Structure & Module Organization

This repository is a prototype-stage implementation and design repository for `mwmap`. `src/mwmap/` contains the CLI implementation, `mwmap.py` is the source-checkout entry point, `docs/` contains notes, and `tests/` contains pytest specs. The `mwsync` path is a gitignored symlink to the sibling `mwsync` project and should not be treated as part of this repo.

Current Python source layout:

```text
src/
  mwmap/
    cli.py
    workspace.py
    commands/
    core/
```

Keep the structure modest. Add modules only when working code needs them. The typical call stack is `mwmap.py -> mwmap.cli.main() -> mwmap.commands.<verb> -> mwmap.workspace/core`.

## Build, Test, and Development Commands

There is no build system or package metadata yet.

- `git status --short`: check local changes before editing.
- `git log --oneline`: review the short commit-history style.
- `rg <term>`: search repository text quickly.
- `python3 -m pytest -q`: run the current CLI milestone tests.

When Python packaging is introduced, document the exact commands here before relying on them.

## Coding Style & Naming Conventions

No formatter or linter is configured yet. Prefer clear module names, small command modules, and explicit names that match CLI verbs, such as `commands/init.py`, `commands/status.py`, or `commands/clone.py`. Give each function a brief docstring and add short call-stack notes where a module coordinates several layers.

Use Markdown headings consistently in design documents. Keep prose direct and mark speculative architecture as tentative.

## Testing Guidelines

The current tests describe the local first-version `mwmap.py` CLI behavior. Do not write tests concurrently with implementation unless explicitly requested. If tests are missing, incomplete, or not targeted enough for safe changes, stop and ask before editing.

Prefer `pytest` unless the project adopts a different framework. Name tests by behavior, for example `test_status_reports_unpaired_pages.py`. Document any fixtures that require MediaWiki credentials, local notebooks, or external services.

## Commit & Pull Request Guidelines

Recent commits use concise, sentence-style summaries, for example `Clarify relationship between mwsync and mwmap`. Follow that style: describe the change plainly without prefixes unless a convention is later adopted.

Pull requests should include a short purpose statement, a summary of changed files, and any assumptions about the evolving `mwmap`/`mwsync` relationship. Link related issues or design notes when available. Include command output for tests.

## Agent-Specific Instructions

Do not edit through the gitignored `mwsync` symlink. Keep contributor guidance aligned with the prototype status and avoid inventing commands, package names, or workflows not present in the repo.
