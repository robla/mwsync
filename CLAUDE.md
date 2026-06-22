# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

`mwmap` is a **prototype/design-stage** CLI for keeping MediaWiki content paired
with other local wiki-like formats (Zim notebooks, Org-mode files, Markdown
trees). The core abstraction is a **map**: rules describing how wiki objects
(page, subtree, namespace, whole wiki) correspond to local structures, with
two-way sync as the long-term goal.

`src/mwmap/` is the current first-version CLI implementation. The root
`mwmap.py` is a thin source-checkout entry point that imports `mwmap.cli`.
The repository also holds design docs (`README.md`, `docs/`), contributor
guidance (`AGENTS.md`, `GEMINI.md`), and a small pytest suite covering local
CLI behavior. Much of `git log` is still design history rather than
implementation history.

## Commands

- `python3 -m pytest -q` — run the current fast local CLI tests.
- `python3 mwmap.py --help` — show the current CLI surface.
- `python3 mwmap.py init && python3 mwmap.py clone https://electowiki.org/wiki/California` — smoke-test the first networked clone workflow.
- `rg <term>` — search repository text.

## First-version target (the spec the tests encode)

The first CLI is implemented under `src/mwmap/`, with root `mwmap.py` kept for
source-checkout execution (the tests hardcode `PROJECT_ROOT / "mwmap.py"`).
Its motivating run is `init` then
`clone <page-url>` in an empty directory. `clone` contacts MediaWiki; the other
commands operate on local mapping metadata. Required behavior:

- Global `--root PATH` option, defaulting to the current directory. The root is
  the implicit local working tree (never registered); everything synced against
  is a registered **remote** (Git's asymmetric model — a remote may be local).
- `--help` shows `init`, `clone`, `remote`, and `status`.
- `init` creates `_mwmap/config.yaml` and `_mwmap/cache/`, writing config
  equal to `{version: 1, remotes: {}, mappings: []}`, and prints "initialized".
- `remote add NAME TYPE LOCATION` records `remotes[NAME] = {type, location}`.
- `clone URL [PATH]` onboards a MediaWiki page URL end to end — registers a
  remote from the URL, pairs, fetches from MediaWiki, writes the local file;
  inits if needed.
- `status` reports configured remotes and the mapping count (e.g. `0 mappings`).
- Commands needing config must exit nonzero with a clear message before `init`.

See `tests/test_mwmap_cli.py` for exact expected output strings.

## Architecture and metadata model

- `_mwmap/config.yaml` is **durable, user-facing** state: remote definitions and
  mapping rules. `_mwmap/cache/` is **disposable** — anything repopulatable from
  remotes. Do not create `_mwmap/refs/` (it implies a git-like ref store mwmap
  shouldn't inherit); if revision storage is needed later, prefer
  `_mwmap/revisions/`.
- Subcommands are **verb-style** and deliberately aligned with Git semantics
  (`init`, `fetch`, `status`, `diff`, `merge`, `push`, `restore`, `log`,
  `checkout`, …). `docs/git-mapping.md` maps Git ↔ legacy `mwsync.py` ↔ `mwmap`
  verbs; `docs/architecture.md` is the source of truth for direction. Note the
  intended split versus legacy `mwsync`: **pairing/config** (`remote`, `pair`)
  is separate from **content sync** (`fetch`, `merge`, `push`).
- Verbs may eventually need to become `mwsync` verbs (e.g. `mwmap init` →
  `mwsync mapinit`). Choose names with that migration path in mind — `mwmap`
  may become "mwsync 2.0", a plugin to it, or an `mwsync` rearchitecture, and
  that relationship is deliberately unresolved.

## Working conventions specific to this repo

- **YAGNI is a stated rule.** The project has started the
  `src/mwmap/{cli,commands/,core/}` layout, but modules should stay small and
  should exist only when working code needs them. The normal call stack is
  `mwmap.py -> mwmap.cli.main() -> mwmap.commands.<verb> -> context/core`.
- Each function should have a brief docstring. Use short call-stack notes in
  modules that coordinate multiple layers; avoid large explanatory comments.
- **Do not write tests concurrently with implementation unless explicitly
  asked.** If existing tests are missing, incomplete, or not targeted enough to
  make a change safely, stop and ask first. This matters most for sync, merge,
  page-identity, and revision-state behavior. Each test starts with a short
  (<500 char) comment stating its intent.
- The `mwsync` symlink points to the sibling `mwsync` project. It is gitignored
  and **not part of this repo — never edit through it.**
- Commit messages: concise, sentence-style summaries, no prefixes (e.g.
  "Clarify relationship between mwsync and mwmap"). Recent multi-model commits
  append an attribution suffix like `(Claude)` or `(Gemini)`.

## Multi-LLM collaboration

This repo is edited by several models (ChatGPT, Gemini, Claude). `docs/llm-log.org`
is a shared work log. **After a substantive change, add your own one-line entry**
in its Org format:

```
** Claude [YYYY-MM-DD Ddd HH:MM]: short description of the substantive change
```

One entry per substantive edit (skip trivial churn). Log only your *own* work —
do not write entries on another model's behalf.
