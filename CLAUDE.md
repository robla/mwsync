# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

`mwmap` is a **prototype/design-stage** CLI for keeping MediaWiki content paired
with other local wiki-like formats (Zim notebooks, Org-mode files, Markdown
trees). The core abstraction is a **map**: rules describing how wiki objects
(page, subtree, namespace, whole wiki) correspond to local structures, with
two-way sync as the long-term goal.

**There is almost no code yet.** `mwmap.py` — the planned CLI entry point — does
*not* exist. The repository currently holds design docs (`README.md`, `docs/`),
contributor guidance (`AGENTS.md`, `GEMINI.md`), and a failing test suite that
specifies the first milestone. `git log` is a history of design decisions, not
implementation.

## Commands

- `python3 -m pytest -q` — run the milestone tests. **These currently fail by
  design**: they invoke `mwmap.py` (repo root), which is unimplemented. They are
  the spec for the first version, not a regression suite.
- `python3 mwmap.py --help` — the target CLI, once implemented.
- `rg <term>` — search repository text.

## First-version target (the spec the tests encode)

The first `mwmap.py` is a single small CLI at the **repository root** (the tests
hardcode `PROJECT_ROOT / "mwmap.py"`). Its motivating first run is `init` then
`clone <page-url>` in an empty directory. `clone` contacts MediaWiki; the other
commands operate on local mapping metadata. Required behavior:

- Global `--root PATH` option, defaulting to the current directory. The root is
  the implicit local working tree (never registered); everything synced against
  is a registered **remote** (Git's asymmetric model — a remote may be local).
- `--help` shows `init`, `clone`, `remote`, and `status`.
- `init` creates `_mwmap/config.yaml` and `_mwmap/cache/`, writing config
  equal to `{version: 1, remotes: {}, mappings: []}`, and prints "initialized".
- `remote add NAME TYPE LOCATION` records `remotes[NAME] = {type, location}`.
- `clone URL [PATH]` onboards a page (or wiki) end to end — registers a remote
  from the URL, pairs, fetches from MediaWiki, writes local files; inits if needed.
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

- **YAGNI is a stated rule.** Keep `mwmap.py` a single file while it stays
  trivial. Only expand toward the `src/mwmap/{cli,commands/,core/}` layout
  sketched in `docs/architecture.md` when working code actually needs it — that
  layout is tentative, not a target to build out preemptively.
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
