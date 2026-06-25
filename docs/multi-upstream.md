# Multi-Upstream Design

`mwmap` should support one local file tracking more than one upstream object.
This is useful for page-at-a-time mirroring: for example, one local
`California.mw` file may track an Electowiki article, a Wikipedia draft, and a
private staging wiki page.

The model should be Git-like without copying Git's ref storage. A local file is
the working artifact. Each upstream records the remote object it tracks and the
remote-specific revision that has been incorporated into the local file.

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
