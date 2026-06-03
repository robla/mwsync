# mwsync-ng: ChatGPT Architecture Proposal

Status: opinionated design note, June 2026.

This document assumes `mwmap` is the next-generation implementation path for
`mwsync.py`, and that the eventual user-facing tool may again be named
`mwsync.py` or `mwsync`. In that world, `mwmap` is not a side project forever;
it is the prototype name for the architecture that replaces the current
single-file `mwsync.py`.

## Thesis

The next generation should combine two things:

- `mwmap`'s explicit mapping model: sources, mappings, local stores, and
  non-1:1 relationships.
- `mwsync.py`'s hard-won sync model: fetched upstream refs, base refs, pending
  commits, merge state, revision history, and cautious push/reconcile behavior.

The current `mwsync.py` should not be copied into a larger monolith. Its sync
engine should be extracted, made library-friendly, and mounted under `mwmap`'s
more general project model.

## Design Principles

Keep the architecture boring where correctness matters. MediaWiki sync needs
clear refs, durable cache files, explicit conflict states, and predictable CLI
behavior more than it needs clever abstraction.

The mapping layer should be flexible, but the sync layer should be conservative.
It should fail clearly instead of guessing, avoid implicit network activity in
status-like commands, and keep migration code separate enough to delete later.

The first production milestone should be feature parity for the current
one-page-to-one-`.mw` workflow. Namespace, subtree, Zim, Org, and whole-wiki
features justify the architecture, but they should not block replacing the tool
the user already relies on.

## Project Model

A next-gen workspace should have one durable project directory:

```text
_mwmap/              # prototype name
  config.yaml        # durable user-facing configuration
  cache/             # disposable source-derived state
```

If `mwmap` is formally renamed to `mwsync`, this directory should probably
become `_mwsync/` before broad use. Do not carry both names as permanent
first-class formats. Pick one, provide a one-time migration, and move on.

The durable config should model:

- `sources`: named systems such as Electowiki, enwiki, a local file tree, a Zim
  notebook, or an Org store.
- `mappings`: explicit relationships between source objects.
- `defaults`: project-wide behavior such as namespace filename policy, preview
  behavior, and sync safety knobs.

Example shape:

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

The old `mwsync.yaml` one-wiki model maps cleanly onto this: one MediaWiki
source, one local `mwfiles` source, and one page mapping per tracked article.

## Source Adapters

`mwmap` should be built around source adapters, but only after the first simple
CLI exists. Each adapter should expose a small interface:

- identify objects, such as MediaWiki page titles or local file paths;
- read current content and metadata;
- list objects when supported;
- write content when supported;
- report enough revision identity for safe synchronization.

The first adapters should be:

- `mediawiki`: Action API access for page bodies, revision metadata, login,
  edit, parse preview, and file metadata.
- `mwfiles`: local `.mw` files with the current namespace filename policy.

Later adapters can support Zim, Org, Markdown, or other formats. Those should
not be designed deeply until page-to-file parity works.

## Mapping Layer

Mappings are durable user intent. They are not cache state.

The mapping layer should support these shapes over time:

- `page`: one MediaWiki page to one local object.
- `namespace`: one MediaWiki namespace to one local directory.
- `subtree`: a title-prefix or page-tree relationship.
- `wiki`: broad whole-wiki mapping, probably with namespace filters.
- `split` / `combine`: future many-to-one or one-to-many mappings.

Only `page` needs to exist for the first real migration. It should be expressive
enough to carry the current `mwsync.py` workflow without inventing new behavior.

## Sync State

Sync state should live under `_mwmap/cache/`, scoped by source and mapping id.
The cache is disposable in theory, but it is valuable reviewable state in
practice, so keep it flat and readable.

Recommended page-mapping cache:

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

This preserves the current model:

- `upstream`: latest fetched remote revision.
- `base`: revision the local working copy is based on.
- `last-pushed`: latest revision known to have been authored through this
  workflow, including browser-save reconciliation.
- `history.jsonl`: chronological metadata ledger.
- `commit.*`: pending local proposal for push.
- `merge.json`: unresolved merge state.

Using `refs/` is acceptable here. `mwmap` should avoid gratuitous Git mimicry,
but this storage really does behave like lightweight refs.

## Command Model

The CLI should first preserve the daily `mwsync.py` verbs:

```text
init
source add
add / pair page
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

Once the tool is renamed back to `mwsync`, the common page workflow should feel
like today's command set:

```bash
mwsync init
mwsync checkout Software
mwsync diff
mwsync preview Software
mwsync push Software
```

`mwmap pair page ...` is useful during prototype development, but `checkout`
should remain the friendly page-sync command once this becomes `mwsync` again.

## Preview And Reconciliation

The current preview work is important enough to port directly. Preview should
support both local pending commits and uncommitted working-file proposals.

If the user saves through the browser, reconciliation should adopt the remote
revision when lineage proves it descends from the expected base. The remote
revision is authoritative: local refs, cache, working file, YAML metadata, and
the displayed remote change message should come from the wiki-recorded revision.

This behavior is specific to MediaWiki page mappings, but the concept is
general: a source adapter may provide a "recognize my save" operation that turns
an externally-authored write into local committed state.

## Cache Indexes

Wiki-level indexes should remain separate from per-page sync state:

```text
_mwmap/cache/electowiki/indexes/categories/
_mwmap/cache/electowiki/indexes/titles/
_mwmap/cache/electowiki/indexes/files/
```

This is the next-gen equivalent of `_cache/_categories` and `_cache/_titles`.
These indexes are source-derived, refreshable, and not durable mapping intent.
Category mappings such as `catmap.yaml` are durable human decisions and should
remain outside disposable cache state unless the config model grows a dedicated
durable mapping section.

## Implementation Shape

Avoid another giant script. A modest package layout is enough:

```text
src/mwmap/
  cli.py
  config.py
  workspace.py
  sources/
    mediawiki.py
    mwfiles.py
  sync/
    refs.py
    history.py
    merge.py
    commit.py
    push.py
    preview.py
  commands/
    init.py
    source.py
    checkout.py
    fetch.py
    status.py
```

The sync modules should raise exceptions and return structured results. The CLI
layer should decide how to print and when to exit. This is the single biggest
architectural correction from current `mwsync.py`, where many helpers print and
`sys.exit()` directly.

## Migration Strategy

Migration should be a one-time converter, not a permanent compatibility layer.

For an existing `mwsync.yaml` checkout, migration should:

- create `_mwmap/config.yaml`;
- create one `mediawiki` source from `wiki.api_base`;
- create one local `mwfiles` source rooted at `.`;
- create one `page` mapping per `wiki.articles` entry;
- move or copy `_cache/<Article_Key>/` state into the new cache layout;
- preserve `upstream_*`, `last_pushed_*`, refs, history, pending commits, and
  merge state;
- leave a clear report of what changed.

Support `--dry-run`. Keep ugly legacy detection inside the migration command,
not in normal sync code.

## Recommended Sequence

1. Finish `mwmap` v0.01: `init`, `source add`, `status`, and tests.
2. Extract `mwsync.py` sync primitives into importable, raise-not-exit modules.
3. Implement `mwfiles` + `mediawiki` page mappings using those primitives.
4. Port page commands to `mwmap`: `checkout`, `fetch`, `merge`, `diff`,
   `status`, `commit`, `preview`, `push`, `restore`, `log`, `show`, `fsck`.
5. Add one-way `mwsync.yaml` / `_cache` migration.
6. Rename the user-facing command to `mwsync` once parity is real.
7. Freeze or retire old `mwsync.py`.
8. Only then expand into namespace, subtree, Zim, Org, and whole-wiki mappings.

## My Take

Using `mwmap` as the next-gen architecture is the right direction if the goal is
more than single-page `.mw` mirrors. The explicit source/mapping model is the
missing abstraction in current `mwsync.py`.

But `mwmap` should not become a grand rewrite that postpones the working
workflow. The fastest safe path is parity first: port the current page sync
engine under the new mapping model, preserve the Git-like commands that already
work, and defer fancy mappings until the replacement can handle Electowiki daily
use better than the old script.
