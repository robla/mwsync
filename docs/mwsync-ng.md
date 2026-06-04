# mwsync-ng Migration Plan

Status: merged working plan, June 2026. Rob has now answered the open questions
that ChatGPT raised at the bottom of this file; the settled answers are
summarized under "Settled Decisions" below and folded into the body. The items
that remain genuinely undecided are called out explicitly.

This document merges the current user plan with the Claude, Gemini, and ChatGPT
next-generation notes. The individual files (`docs/mwsync-ng-CLAUDE.md`,
`docs/mwsync-ng-GEMINI.md`, `docs/mwsync-ng-CHATGPT.md`) remain useful
background, but this document should be treated as the current coordination
plan.

## Sources Merged

- The user plan: grow the new `mwmap` work into the next `mwsync`, preserve the
  public `robla/mwsync` history, keep `mwmap` as a separate temporary repo, and
  eventually merge or replace repository histories in a deliberate way.
- Claude's plan: use a strangler-fig migration, keep `mwsync.py` working while
  porting its sync engine into the more general `mwmap` topology.
- Gemini's plan: separate durable configuration from disposable cache state,
  move from implicit `_cache` state to explicit `_mwmap` or `_mwsync`
  configuration, and make mappings explicit.
- ChatGPT's plan: use `mwmap`'s source/mapping model plus `mwsync.py`'s
  existing sync engine; reach page-to-`.mw` parity before expanding into larger
  mapping shapes.

## Consensus Direction

`mwmap` should become the next-generation implementation of `mwsync`. The name
`mwmap` is useful while the architecture is experimental, but the likely final
user-facing command and GitHub project should be `mwsync`.

The core architectural decision is:

- `mwmap` provides the new topology: sources, mappings, local stores, page /
  namespace / subtree / wiki relationships.
- `mwsync.py` provides the proven sync behavior: refs, base/upstream tracking,
  revision cache, pending commits, merge state, preview reconciliation, and
  push safety.

The first production milestone is not "all of mwmap." It is replacing the
current one-page-to-one-`.mw` `mwsync.py` workflow with a modular implementation
that behaves at least as well as the old tool.

## Settled Decisions

These are decided as of Rob's June 2026 answers and should be treated as fixed
unless he reopens them:

- **Final metadata directory: `_mwsync/`.** The `mwmap.py` prototype creates
  `_mwmap/` (its tests assert that), but the production directory is `_mwsync/`.
  Plan for a rename at adoption rather than shipping `_mwmap/` as permanent.
- **Initial executable: `mwmap.py`.** The new code ships first as `mwmap.py`, a
  helper script alongside the existing suite. The final user-facing command
  becomes `mwsync` again, and `map` may survive as a next-generation `mwsync`
  *verb* (e.g. `mwsync map ...`) rather than a separate binary.
- **Canonical repo: `robla/mwsync`.** Its `main` branch should eventually carry
  the new implementation. (How the histories get combined is still open — see
  Repository Strategy.)
- **Multiple tracked remotes are a goal**, not something to design out. Rob
  wants a git-like model where one workspace can fetch/merge from one remote and
  push to a different one. The old "one wiki per working directory" rule should
  be relaxed rather than preserved. See "Multiple Remotes" below.
- **Avoid git-flavored names only when the data is not git-compatible.** Rob is
  leaning *into* a genuinely git-like model (clonable workspaces, linear
  history, tracked remotes). Where the underlying structure really is
  git-compatible, names like `refs`, `remote`, and `clone` are honest and
  welcome; avoid them only where they would misrepresent a non-git structure.
- **`mwsync migrate` is distant-future work.** A one-time automated
  `_cache` → `_mwsync` migration is wanted eventually, but it is explicitly not
  an early milestone. Near-term, old and new state may simply coexist for manual
  testing; the automated converter comes much later, and only once it exists
  should the old `_cache/` become deletable.

Still open: how to combine git histories (Q4), whether to merge the `mwmap`
repo in early or keep it separate until parity (Q5), and the exact long-term
lifetime of old `_cache`/`mwsync.yaml` compatibility (Q8).

## Repository Strategy

The likely repository end state is that
[https://github.com/robla/mwsync](https://github.com/robla/mwsync) remains the
canonical public repository for the tool now called `mwsync`, while the current
`mwmap` work is merged into or becomes that repository.

During development, keep the temporary `mwmap` repository separate. It gives the
new architecture a clean space to form. Do not edit the sibling repo through the
untracked `mwmap` symlink inside this repo; work in `/home/robla/src/mwmap`
directly when changing that project.

Eventually choose one of these history strategies:

- Merge `mwmap` into `mwsync` with history, probably as a subtree or directory
  import, then rename/move files into the final layout.
- Make `mwmap` the new repository content and graft/import old `mwsync` history
  in a way that keeps old commits reachable.
- Keep the old `mwsync` history as archival branches/tags and replace the main
  branch with the new implementation.

Do not decide this casually. The repository move is separate from the code
architecture. The code can be made ready first, then the repository history can
be merged with a planned one-time operation.

## Workspace Model

The new project metadata should live in one tool-owned directory per working
tree. While `mwmap` is experimental, that directory is:

```text
_mwmap/
  config.yaml
  cache/
```

The final metadata directory is `_mwsync/` (decided; see Settled Decisions). The
`mwmap.py` prototype uses `_mwmap/`, so treat that as transitional and plan a
rename at adoption rather than carrying `_mwmap/` forever. The old `_cache/` and
the new directory should be allowed to coexist during the transition. That lets
the old `mwsync.py` and the new prototype be tested in the same working checkout
without destructive conversion.

The durable config should define sources and mappings:

```yaml
version: 1
sources:
  electowiki:
    type: mediawiki
    api_base: https://electowiki.org/w/api.php
  local:
    type: mwfiles
    root: .
mappings:
  - id: electowiki:page:Software
    type: page
    from: electowiki:Software
    to: local:Software.mw
```

The current `mwsync.yaml` maps mechanically to this model: one MediaWiki source,
one local `.mw` file source, and one page mapping per `wiki.articles` entry.

## Multiple Remotes (Git-Like Tracking)

Rob wants the next-gen tool to behave like git with respect to remotes: a single
workspace should be able to track more than one remote source and route
different operations to different remotes. The motivating use case is fetching
and merging from one remote `_mwsync` repository while pushing to a *different*
one.

This has consequences worth designing for early, even if the implementation
lands later:

- A source is not necessarily a live MediaWiki API. It can also be another
  `_mwsync` workspace, or a non-MediaWiki store (e.g. a Zim directory) presented
  *as* a remote. Rob has floated turning a zimwiki directory into a remote that
  `mwsync` can "clone", where the clone carries a linear history of some sort.
- Because clone/fetch/push and linear history are explicitly desired, prefer
  storage structures that are genuinely git-compatible. That is what makes the
  git vocabulary (`refs`, `remote`, `clone`) honest rather than cosmetic.
- The single-wiki guard that `mwsync.py` enforces today should be relaxed, not
  reimplemented. A workspace mapping multiple sources is the target, not an
  error.

This is aspirational and should not block page-to-`.mw` parity, but the config
and cache shapes chosen for parity should not paint multi-remote into a corner.

## Cache And Sync State

The next-gen cache should separate wiki-level indexes from per-page sync state.
Per-page state should preserve the current ref model:

```text
_mwmap/cache/electowiki/pages/Software/
  history.jsonl
  refs/upstream
  refs/base
  refs/last-pushed
  revisions/19737.mw
  revisions/19737.json
  commit.mw
  commit.json
  merge.json
  preview.html
```

Wiki-level indexes should be source-scoped:

```text
_mwmap/cache/electowiki/indexes/categories/
_mwmap/cache/electowiki/indexes/titles/
_mwmap/cache/electowiki/indexes/files/
```

The layout above uses `refs/` deliberately. Rob wants a genuinely git-like model
(tracked remotes, clone, linear history), and where the structure really is
git-compatible the git vocabulary is honest. If a particular piece of state
turns out *not* to be git-compatible, rename that piece rather than forcing a
misleading `refs/` label onto it.

The cache is disposable in principle, but it is still reviewable operational
state. Keep it readable, mostly flat, and easy to inspect with ordinary shell
tools.

## Implementation Shape

Do not create another giant script. The new implementation should have a small
package layout once it grows beyond the first prototype:

```text
src/mwmap/
  cli.py
  config.py
  sources/
    mediawiki.py
    mwfiles.py
  core/
    revmgr.py
    synchronizer.py
  commands/
    init.py
    checkout.py
    fetch.py
    status.py
```

The sync code should raise exceptions and return structured results. The CLI
layer should do printing and `sys.exit()`. This is a deliberate correction from
current `mwsync.py`, where many helpers are hard to reuse because they directly
print and exit.  None of the `core/` files should have names that look like verbs used for subcommands.

## Command Parity Target

The first real replacement must support the daily page workflow:

```text
init
source add
checkout
fetch
merge
diff
status
commit
preview
push
restore
log
show
fsck
migrate
```

Prototype commands such as `mwmap pair page ...` are acceptable while developing
the mapping model. Once the tool becomes `mwsync` again, `checkout`, `fetch`,
`merge`, `diff`, `preview`, and `push` should remain the familiar user-facing
verbs.

## Migration Plan

### Phase 1: Finish the `mwmap` local-metadata prototype

Implement and test:

```bash
mwmap init
mwmap source add electowiki mediawiki https://electowiki.org/w/
mwmap status
```

This phase should not contact MediaWiki. It only establishes the new config and
workspace shape.

### Phase 2: Extract reusable sync primitives from `mwsync.py`

Move or copy the current MediaWiki API, cache/ref, merge, commit, push, preview,
and fsck logic into importable modules. Keep the old CLI working while doing
this. Avoid behavior rewrites unless needed to make the code reusable.

### Phase 3: Implement page mappings with parity

Teach `mwmap` how to sync one MediaWiki page to one local `.mw` file. This is
the point where `mwmap` becomes a credible next-gen `mwsync`, not just a mapper.

The parity target includes preview reconciliation, no-arg `diff`, local-only
`status`, pending pushes, merge conflict state, and push metadata.

### Phase 4: Support side-by-side migration

This phase has two clearly separated parts, and only the first is near-term.

Near-term (cheap): allow `_cache/`, `mwsync.yaml`, and the new directory
(`_mwmap/` during the prototype, `_mwsync/` after adoption) to coexist. This is
just non-destructive parallel state so both tools can be exercised in one
checkout; it needs no converter.

Distant-future: the automated `mwsync migrate` command. Per Rob this is explicit
"miles away" work, not an early milestone. When it does land it should read old
state and create new state without requiring immediate deletion of old files,
and once it exists the old `_cache/` becomes deletable.

When built, the migration should:

- create the new config;
- create sources and page mappings;
- migrate per-article refs, history, revision bodies, metadata, pending commits,
  merge state, and push metadata;
- preserve local working files;
- support `--dry-run`;
- keep legacy detection and ugly edge cases isolated inside migration code.

### Phase 5: Decide and execute the repository merge

Once the new implementation can replace the old daily workflow, decide how to
combine the histories and update the public `robla/mwsync` repository. This
should be a planned git operation, not an incidental refactor.

### Phase 6: Rename and retire

After parity and migration are credible:

- rename the user-facing command to `mwsync`;
- complete the rename of the metadata directory to `_mwsync/` (decided);
- freeze or deprecate old `mwsync.py`;
- remove long-lived compatibility paths after migration has done its job.

### Phase 7: Expand beyond page mappings

Only after parity should the project expand into namespace, subtree, whole-wiki,
Zim, Org, Markdown, and richer category/file workflows.

## Question Log (Rob's Answers Inline)

Rob has answered these inline below. Most are now settled and are summarized
under "Settled Decisions". The ones still genuinely open are Q4 (history-
combination technique), Q5 (merge the `mwmap` repo early vs. wait for parity),
and Q8 (compatibility lifetime). Q10 was unclear as asked and is reframed below.

1. Should the final metadata directory be `_mwsync/`, `_mwmap/`, or something else?
The final metadata directory should be `_mwsync/`.
2. Should the final executable be `mwsync`, `mwsync.py`, `mwmap`, or should
   `mwmap` remain as an alias/subcommand during transition?
Eventually, "map" may become a next-generation verb.  "mwmap.py" should be the initial executable, though.
3. Should the GitHub repository at `robla/mwsync` eventually contain the new implementation on its main branch?
Yes
4. How should histories be preserved: subtree merge, graft/filter-repo, archival branches, or another approach?
I don't know.  Let's document this as a decision that needs to be made.
5. Should `mwmap` be merged into this repo early, or stay separate until page parity is reached?
Unsure.
6. Should next-gen `mwsync` keep the old one-wiki-per-working-directory rule, or should multiple MediaWiki sources be allowed in one workspace?
I'm not sure.  I would love to make it so that multiple sources can be allowed in one workspace, so that it's possible to fetch/merge from one remote _mwsync repo, and push to a different _mwsync repo.  I think I want this to work like git somehow, with multiple remotes that get tracked.
7. During migration, should `_cache` be copied, moved, or left in place with the new cache rebuilt from it?
I will eventually want to make "mwsync migrate" work for a one-time migration of _cache to _mwsync, but we are miles from that day.
8. How long should old `mwsync.yaml` / `_cache` compatibility remain after the migration command exists?
I'm not sure.  I think once "mwsync migrate" is completed, then the old "_cache" directory should be deletable.
9. Should `ledecopy.py`, `catmgr.py`, and `wikimgr.py` become subcommands of next-gen `mwsync`, or remain separate helper scripts?
Separate helper scripts for now.  It may be that mwmap.py becomes a new helper script in the suite.
10. Is page-to-`.mw` parity the only scope for the first replacement release, or should category/title indexes also be included?
I'm not sure I understand this question
   (Reframed: when we ship the first page-to-`.mw` replacement, do we *also*
   build the wiki-level indexes — the category and title caches that `catmgr.py`
   and friends rely on — or do we ship per-page sync alone first and add those
   index caches later? Recommendation: per-page parity first, indexes later.)
11. Should `refs/` remain the sync-state name in the new cache, or should the project avoid Git-flavored names such as `refs`?
Avoid git-flavored names if the underlying data structures are not git compatible.  I may want to turn a zimwiki directory into a "remote" that can be "clone"d by mwsync (where the clone includes a linear git history of some sort)
12. Should the first migration tool be implemented in old `mwsync.py`, new `mwmap`, or a one-off standalone script?
Probably part of `mwsync migrate`.  TBD.  That should be the distant future.

## Current Recommendation from ChatGPT

Keep `mwmap` separate for now, finish its local metadata prototype, and start
extracting reusable sync modules from `mwsync.py`. Do not merge repository
histories until page-to-`.mw` parity is real enough to make the new code the
default daily tool.

When the code is ready, prefer making `robla/mwsync` the canonical final
repository, with old history preserved and `mwmap` history imported rather than
discarded. The exact git technique should be chosen after the code architecture
is settled.

## Advice for Claude

Operational guidance for any Claude session picking up this migration. The deeper
rationale and the strangler-fig framing live in `docs/mwsync-ng-CLAUDE.md`; this
is the short version that belongs in the shared plan.

- Treat "Settled Decisions" as binding: `_mwsync/` is the final directory,
  `mwmap.py` is the first executable, `robla/mwsync` is the canonical repo. Do
  not relitigate these; do flag it if new work contradicts them.
- Do the cheap, decision-free work first: make `mwmap.py` v0.01 pass its own
  tests, and start carving reusable, raise-not-exit sync primitives out of
  `mwsync.py`. Neither needs the still-open questions resolved.
- Keep the familiar verbs (`init`, `checkout`, `fetch`, `merge`, `diff`,
  `preview`, `push`). Prototype spellings like `pair page` or `mapinit` are fine
  while the mapping model is being built, but they are scaffolding, not the
  destination — do not propose renaming the verbs Rob already relies on.
- Design the parity config and cache so multi-remote (git-like tracking) stays
  possible later. Do not hard-code a single-wiki assumption into new code the
  way `mwsync.py` does.
- Do not build `mwsync migrate` early. It is distant-future work; near-term,
  let old and new state coexist for manual testing.
- Do not edit the sibling `mwmap` repo through the `mwmap` symlink in this repo.
  Work in `/home/robla/src/mwmap` directly, and respect that repo's own
  `AGENTS.md`.
- When in doubt on the still-open items (history-combination technique, early
  repo merge, compatibility lifetime), document the tradeoffs and ask rather
  than choosing silently.
