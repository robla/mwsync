# Multi-Upstream Design

`mwmap` should support one local file tracking more than one upstream object.
This is useful for page-at-a-time mirroring: for example, one local
`California.mw` file may track an Electowiki article and a Crostini staging wiki
page.

The model should be Git-like without copying Git's ref storage. The local file
is the working artifact. Each upstream records the remote object it tracks and
the remote-specific revision that has been incorporated into the local file.

## Config File Name

The durable project config is `_mwmap/mwmap.yaml`.

`_mwmap/config.yaml` was the prototype name. Implementations should read it as
a legacy fallback for now, but new workspaces and saves should write
`_mwmap/mwmap.yaml`. This rename is settled; do not revert to `config.yaml`.

## No Load-Bearing Version Number

The config file should be self-describing. Do not use a top-level `version:`
integer to decide how to read it.

Legacy mappings are identifiable because they have top-level `remote`, `pageid`,
and `base_revid` fields. Multi-upstream mappings are identifiable because they
have an `upstreams:` mapping. A future `migrate` verb should rewrite old shapes
to the current shape intentionally; ordinary reads should not silently perform a
schema upgrade.

## Terms

- **Remote**: a named store, such as `electowiki`, `crostini`, or `wikipedia`.
- **Mapping**: one local artifact. Its durable identity is `local_path`.
- **Upstream**: one remote object attached to a mapping.
- **Upstream key**: the key under `upstreams:`. The first implementation should
  use the remote name as the key.
- **Primary upstream**: the upstream used when a command needs one default.
- **Base revision**: the upstream-specific revision currently incorporated into
  the local file.

## Proposed YAML Shape

Keep remotes global and upstreams local to each mapping:

```yaml
remotes:
  electowiki:
    type: mediawiki
    location: https://electowiki.org/w/
  crostini:
    type: mediawiki
    location: http://penguin.linux.test/mediawiki

mappings:
  - type: page
    local_path: California.mw
    format: mw
    primary_upstream: electowiki
    upstreams:
      electowiki:
        remote: electowiki
        pageid: 2598
        remote_path: California
        base_revid: 16692
      crostini:
        remote: crostini
        remote_path: California
        pageid: null
        base_revid: null
        state: not_created
```

`local_path` is the mapping identity. Do not add a separate `id` unless a later
feature proves that local-file rename tracking needs one.

The upstream key should initially match the remote name. A future extension may
allow two upstreams on the same remote, such as `draft` and `published`, but
that is not needed for the near-term Electowiki/Crostini mirroring workflow.

## Not-Yet-Created Upstreams

A multi-upstream mapping must support a target page that does not exist yet.
That upstream has:

```yaml
pageid: null
base_revid: null
state: not_created
```

Pushing to such an upstream must use a MediaWiki edit-by-title `createonly`
operation, not edit-by-pageid. On success, `mwmap` records the returned `pageid`
and new revision id as that upstream's `pageid` and `base_revid`, and changes
`state` to `tracked`.

This is the key workflow for turning an existing local file into a mirror on a
new wiki.

## Command Defaults

Commands should behave like Git's branch/upstream defaults, but with explicit
care around network writes:

- `fetch PATH` fetches all tracked upstreams for that mapping unless
  `--upstream NAME` is specified. Not-created upstreams are reported and skipped.
- `merge PATH` merges from `primary_upstream` by default.
- `pull PATH` means fetch then merge from `primary_upstream` by default.
- `commit PATH` stages the local file once, independent of any one upstream.
- `push PATH --upstream NAME` pushes the staged content to that upstream.
- `push PATH` pushes to `primary_upstream` by default. Early versions should
  prompt before relying on that default, for example `Push to electowiki?`.
- `status PATH` shows every upstream, including tracked/not-created state,
  fetched-vs-base state, and whether a staged commit exists.

Repo-wide commands may operate on every mapping, but should require explicit
confirmation before a write affects multiple upstreams.

## Verbatim Wikitext Pushes

For MediaWiki-to-MediaWiki mirroring, assume the user wants verbatim wikitext
pushed from the local file or pending commit. This can be a footgun when
templates, categories, or magic words differ between wikis, but `mwmap` is a
power-user tool and should permit it.

Later adapter work may add per-upstream transformations and warnings. The
initial multi-upstream MediaWiki workflow should not block on that machinery.

## Revision State

`base_revid` moves from the mapping level to each upstream. A local file can be
current with Electowiki while still behind Crostini. Advancing one upstream
after a successful merge or push must not advance the others.

The durable source of truth is `_mwmap/mwmap.yaml`. The cache's `page.yaml` may
mirror the base for readability and alias rebuilding, but `fsck` should flag
drift between cache metadata and the durable mapping.

The cache remains remote/pageid keyed:

```text
_mwmap/cache/<remote>/pages/<pageid>/
```

Two upstreams for the same local file therefore keep separate histories and
page metadata.

## Migration From Legacy Shape

Current single-upstream mappings look like this:

```yaml
- type: page
  remote: electowiki
  pageid: 2598
  remote_path: California
  local_path: California.mw
  format: mw
  base_revid: 16692
```

The `migrate` verb should rewrite them mechanically:

```yaml
- type: page
  local_path: California.mw
  format: mw
  primary_upstream: electowiki
  upstreams:
    electowiki:
      remote: electowiki
      pageid: 2598
      remote_path: California
      base_revid: 16692
      state: tracked
```

The migration should be explicit:

```sh
mwmap migrate
```

Before migration is implemented, code may keep accepting the legacy shape.
New multi-upstream work should target the `upstreams:` shape directly.

## Schema Upgrade Priority

Do not stop at a narrow "attach one empty upstream" patch. The next design/code
step should be the schema upgrade itself:

- load both legacy and `upstreams:` mappings;
- implement `mwmap migrate`;
- teach status/fetch/commit/push to operate on the new mapping shape;
- support not-created upstreams via create-only push;
- keep legacy read compatibility until migration is boring.

This makes multi-upstream a first-class model early, instead of layering it as a
special case over the old single-upstream schema.
