# Multi-Upstream Design

`mwmap` should support one local file tracking more than one upstream object.
This is useful for page-at-a-time mirroring: for example, one local
`California.mw` file may track an Electowiki article, a Wikipedia draft, and a
private staging wiki page.

The model should be Git-like without copying Git's ref storage. A local file is
the working artifact. Each upstream records the remote object it tracks and the
remote-specific revision that has been incorporated into the local file.

## Review (Claude, 2026-06-24): unresolved problems

This draft is not ready to drive code. A motivating use case it must serve —
**a workspace whose local `California.mw` already tracks Electowiki now also
publishes to a second MediaWiki whose `California` page does not exist yet** —
exposes gaps the draft does not address. The proposal below this section is left
intact; these are the objections to resolve first. Worst first.

### R1. Scope creep — the `config.yaml` → `mwmap.yaml` rename does not belong here

The rename is orthogonal to multi-upstream, yet it was implemented in code
(`workspace.py`, `init.py`, `remote.py`, `sync.py`) and the milestone tests in
the same commit as this design doc. Problems:

- It contradicts the first-version spec: `CLAUDE.md` and `README.md` say `init`
  creates `_mwmap/config.yaml`. Renaming it moves a documented milestone without
  saying so, and forced edits to `tests/test_mwmap_cli.py`.
- `_mwmap/mwmap.yaml` stutters — `_mwmap/` already namespaces the file, so
  `config.yaml` was unambiguous.
- The "read `config.yaml` as a legacy fallback" rule keeps two on-disk names
  live at once. The next `remote add`/`push` will *silently* rewrite the user's
  `config.yaml` as `mwmap.yaml`. Silent mutation of the durable file is exactly
  what to avoid.

Recommendation: revert the rename, or split it into its own proposal argued on
its own merits. Multi-upstream does not require it.

### R2. `version:` integers in the file are a code smell

A monotonic integer in a hand-edited file conflates *format* with *feature
presence*, and here it is not even load-bearing: the v1 shape is a strict subset
of v2 (a single upstream), and the loader is told to accept both — so
`version: 2` gates nothing. Version numbers with no validator are theater.

Pick one:

- *Load-bearing:* the loader migrates known versions, refuses unknown ones, and
  this doc specifies exactly when the file is rewritten — remembering that
  writing `version: 2` into the user's file is itself a config mutation and must
  not be a silent side effect of a read.
- *Additive, self-describing (preferred):* presence of `upstreams:` vs a
  top-level `remote:`/`pageid:` tells the loader what it is reading. Reserve a
  real version bump for a genuinely breaking change — one where old code would
  silently misread a new file.

### R3. No story for attaching a *new* page on a *new* wiki

`core/mediawiki.mediawiki_edit_page()` edits **by `pageid`** and **requires
`baserevid`** as the edit-conflict guard. A page that does not yet exist on
penguin has no `pageid` and no base revision, so:

- it cannot be expressed by a v2 `upstreams` entry, which mandates `pageid` and
  `base_revid`; and
- it cannot be created by the current push path, which can only edit an existing
  page by id.

The first real use of multi-upstream is "publish to a target that does not have
this page yet." The schema must allow an upstream with no `pageid`/`base_revid`
("not yet created"), and `push` needs an edit-by-title create path
(`createonly`) that records the returned `pageid`+`revid` as that upstream's new
base. The draft is silent on creation.

### R4. Mapping identity is ambiguous

The v2 mapping introduces three candidate identities — `id: california`,
`local_path`, and the per-upstream `(remote, pageid)` — and never says which is
authoritative. By the doc's own definition ("Mapping: one local artifact"), the
natural key is `local_path`. Drop `id`, or, if it is kept, specify its source
(auto-derived? user-assigned?), its uniqueness rule, and whether it survives a
local-file rename. As written it is an unexplained fourth name.

### R5. "One staged blob, many upstreams" assumes cross-wiki content is identical

The command model pushes "the same staged content" to each upstream. But
`docs/hub-format.md` establishes that raw wikitext is site-bound: templates,
categories, and magic words differ per wiki. Electowiki's `California` may carry
electowiki-specific templates/categories that are wrong on penguin or Wikipedia.
The design treats every upstream as an interchangeable sink for one blob and
never acknowledges per-upstream content divergence (which eventually needs
per-upstream working content or an adapter, not a shared blob).

### R6. `push` defaulting to `primary_upstream` is a write-direction footgun

Silently choosing the write target for a network-mutating command is dangerous:
edit `California.mw` thinking of penguin, run `push`, and it goes to electowiki.
Require explicit `--upstream` when a mapping has more than one upstream. Related:
`commit` is defined as upstream-independent, but `push --upstream` guards with
*that upstream's* `base_revid` — a blob committed without reference to B's base
is not safe to push to B unless the file was reconciled with B first. This
base/commit relationship across upstreams is the crux of the whole design, yet
it is parked in Open Questions #1.

### R7. Per-upstream `base_revid` collides with the existing per-page base

`base_revid` already lives in two places: the durable mapping and each cache
`page.yaml`. Moving the mapping copy per-upstream does not reconcile the cache
copy. The draft keeps the cache remote/pageid-keyed (good) but never says which
base is authoritative or that `fsck` must flag drift between them. Name one
source of truth.

### R8. Terminology overload

Four overlapping names — remote, upstream, nickname (the `upstreams:` key), and
`primary_upstream` — and the example collapses the nickname onto the remote name
(`primary_upstream: electowiki`) immediately after the prose says they can
differ. If the nickname layer is kept, illustrate with distinct values
(remotes `electowiki`/`penguin`; nicknames `pub`/`staging`) so the layers are
visibly different. If it is not needed yet, see R9.

### R9. Premature generality (YAGNI)

The "one remote, two upstream pages (draft/published)" case and the
nickname≠remote layer are speculative. For the real near-term need (one file →
electowiki + penguin) `nickname = remote` suffices; mark the general case as a
future extension. Note also that `format` stays at the mapping level, which is
fine for MW-only but contradicts the multi-format ambition (a Google Docs
upstream would not be `mw`).

### Minimum viable implementation

The smallest change set that satisfies the motivating use case — one local file
tracking a second MediaWiki upstream whose page does not exist yet — is:

1. allow an upstream with `pageid: null` / no `base_revid` ("not yet created");
2. add an edit-by-title `createonly` path in `core/mediawiki.py`, used by `push`
   when an upstream has no pageid, recording the returned `pageid`+`revid` as
   that upstream's base;
3. register the second remote and attach a second upstream to the existing
   mapping.

This does not require the full v2 schema below; it is the incremental step that
proves the model before the broader design is committed.

## Config File Name

The durable project config is `_mwmap/mwmap.yaml`.

`_mwmap/config.yaml` was the prototype name. Implementations should read it as
a legacy fallback for now, but new workspaces and saves should write
`_mwmap/mwmap.yaml`.

## Terms

- **Remote**: a named store, such as `electowiki`, `wikipedia`, or `staging`.
- **Mapping**: one local artifact, such as `California.mw`.
- **Upstream**: one remote object attached to a mapping.
- **Primary upstream**: the upstream used when a command needs one default.
- **Base revision**: the upstream-specific revision currently incorporated into
  the local file.

## Proposed YAML Shape

Keep the file understandable by making remotes global and upstreams local to a
mapping:

```yaml
version: 2
remotes:
  electowiki:
    type: mediawiki
    location: https://electowiki.org/w/
  wikipedia:
    type: mediawiki
    location: https://en.wikipedia.org/w/

mappings:
  - type: page
    id: california
    local_path: California.mw
    format: mw
    primary_upstream: electowiki
    upstreams:
      electowiki:
        remote: electowiki
        pageid: 2598
        remote_path: California
        base_revid: 16692
      wikipedia:
        remote: wikipedia
        pageid: 5405
        remote_path: Draft:California voting systems
        base_revid: 123456789
```

`upstreams` is a mapping keyed by a short local nickname. In the common case the
nickname matches the remote name, but it does not have to. A single remote could
provide two upstream pages for one local file, using keys such as `draft` and
`published`.

## Command Defaults

Commands should behave like Git's branch/upstream defaults:

- `fetch PATH` fetches all upstreams for that mapping unless `--upstream NAME`
  is specified.
- `merge PATH` merges from `primary_upstream` by default.
- `pull PATH` means fetch then merge from `primary_upstream` by default.
- `commit PATH` stages the local file once, independent of any one upstream.
- `push PATH` pushes to `primary_upstream` by default.
- `push PATH --upstream NAME` pushes the same staged content to another
  upstream using that upstream's `base_revid`.
- `status PATH` should show every upstream, including fetched-vs-base state and
  whether a staged commit exists.

Repo-wide commands may operate on every mapping, but should require explicit
disambiguation when a write could affect multiple upstreams unexpectedly.

## Revision State

`base_revid` must move from the mapping level to each upstream. A local file can
be current with Electowiki while still behind Wikipedia. Advancing one upstream
after a successful merge or push must not advance the others.

The cache remains remote/pageid keyed:

```text
_mwmap/cache/<remote>/pages/<pageid>/
```

That means two upstreams for the same local file naturally keep separate
histories and page metadata.

## Migration From Version 1

Current v1 mappings look like this:

```yaml
- type: page
  remote: electowiki
  pageid: 2598
  remote_path: California
  local_path: California.mw
  format: mw
  base_revid: 16692
```

They should migrate mechanically to one-upstream v2 mappings:

```yaml
- type: page
  id: california
  local_path: California.mw
  format: mw
  primary_upstream: electowiki
  upstreams:
    electowiki:
      remote: electowiki
      pageid: 2598
      remote_path: California
      base_revid: 16692
```

Until migration is implemented, code may keep accepting the v1 shape. New
multi-upstream work should target the v2 shape.

## Open Questions

- Should a pending commit be per mapping or per upstream? The likely answer is
  per mapping, with push recording per-upstream results.
- Should `fetch PATH` default to all upstreams or only `primary_upstream`? The
  Git-like answer is all; the safer first implementation may be primary only.
- How should conversion warnings be stored when upstream formats differ?
- Should `primary_upstream` also imply the default preview parser?
