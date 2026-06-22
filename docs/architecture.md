# Architecture

`mwmap` is still in the idea/prototype stage, so this document describes direction rather than a committed implementation.

## Relationship to mwsync

`mwsync.py` is a rather opaque monolith. `mwmap` should avoid repeating that shape if it becomes more than a small experiment.

The relationship between `mwmap` and `mwsync` is still unresolved. `mwmap` may become a plugin or extension to `mwsync`, may be rolled into `mwsync`, or may become the basis for a broader `mwsync` rearchitecture. If `mwmap` is successful, it may effectively become "mwsync 2.0".

Design choices should preserve that flexibility. Avoid command names, data models, or package boundaries that would make future integration with `mwsync` unnecessarily awkward.

## Project File Layout

The file layout is a central part of the architecture. The first local project directory should be:

```text
_mwmap/
  config.yaml
  cache/
```

`_mwmap/config.yaml` is durable user-facing map configuration. It should store remote definitions, mapping rules, and other state that defines the user's intended relationship between systems.

`_mwmap/cache/` is disposable storage. It may hold remote-derived metadata, fetched page bodies, local-store indexes, or other data that can be repopulated from remotes if deleted.

Do not create `_mwmap/refs/` for now. The name implies a git-like reference store, and `mwmap` should not inherit that expectation unless the storage model truly needs it. If revision storage becomes necessary, prefer a plain name such as `_mwmap/revisions/`.

## Planned Command Style

`mwmap` is expected to use verb-style subcommands:

```sh
# Quick start — clone a page in one step
# (init + remote add + pair + fetch + populate):
mwmap clone https://electowiki.org/wiki/California

# Or set things up explicitly:
mwmap init
mwmap remote add electowiki mediawiki https://electowiki.org/w/

mwmap pair page electowiki:ElectoramaNews ElectoramaNews.mw
mwmap pair subtree electowiki:ElectoramaNews/ ElectoramaNews/
mwmap pair wiki electowiki .

mwmap fetch
mwmap status
mwmap diff
mwmap merge
mwmap push
mwmap unpair
```

`mwmap.py` is intended to become the next generation of `mwsync.py`. The leading expectation is a straight rename — `mwmap.py` → `mwsync.py` — with verbs preserved, so each verb should also read naturally as `mwsync.py <verb>` (e.g. `mwsync.py clone`, `mwsync.py fetch`). A less-likely alternative is absorbing `mwmap` into the existing `mwsync` as namespaced subcommands (e.g. `mwmap init` → `mwsync mapinit`). Choose verb names that survive either path. When the rename happens, these design docs and their `mwmap.py` examples will be rewritten accordingly.

## Remotes and the Local Working Tree

`mwmap` follows Git's asymmetric model. The directory you run `mwmap` in — its
`--root`, where `_mwmap/` lives — is the **local working tree**. It is never
registered or named; in a pairing, the local side is simply a path, exactly as
Git never makes you name your working tree.

Everything you sync against is a **remote**, registered with `remote add` and
stored under `remotes:` in `config.yaml`. There can be many remotes (e.g. two
MediaWiki instances), like Git's `origin`, `upstream`, and so on. A remote's
location may itself be local (a directory on disk), just as a Git remote can be
a filesystem path — "remote" denotes another store, not another machine.

`mwmap` resolves "which remote" at two levels, mirroring Git:

* **Per-pairing upstream** — each mapping records the remote and remote location
  it tracks, set at `pair` time (like Git's `branch.<name>.remote`). Operations
  on an already-paired path always know their remote, so no default is needed.
* **A repo-level default remote** — a settable `default_remote` pointer (like
  Git's `origin` / `remote.pushDefault`). It auto-resolves to the sole remote
  when there is only one, and is used when a command is not tied to a specific
  pairing (repo-wide `fetch`/`push`, or `pair` without naming a remote).

A fuller config then looks like:

```yaml
version: 1
remotes:
  electowiki:
    type: mediawiki
    location: https://electowiki.org/w/
default_remote: electowiki
mappings:
  - remote: electowiki
    type: page
    remote_path: ElectoramaNews
    local_path: ElectoramaNews.mw
```

Keep YAGNI in mind: the first milestone covers `init`, page-oriented `clone`,
`remote add`, and `status` — so it does fetch content, via `clone`. With a
single remote (the common first run) the default is simply that sole remote. A
settable `default_remote` pointer for *multiple* remotes, richer per-pairing
upstream management, and subtree/wiki clone remain design direction — not code
to build yet.

## Tentative Source Layout

A larger implementation might use a structure like this:

```text
src/
  mwmap/
    cli.py
    context.py
    commands/
      __init__.py
      init.py
      pair.py
      remote.py
      status.py
      sync.py
      unpair.py
    core/
      __init__.py
      context.py
      misc.py
```

Do not treat this layout as fixed. Keep YAGNI in mind and only expand the structure when working code needs it. Still, subcommands should probably live in their own files rather than in a single `mwmap.py` monolith.

## Core Design Direction

The central abstraction is a map: a set of rules describing how MediaWiki pages, page trees, namespaces, or whole wikis correspond to local structures.

The implementation should preserve page identity, links, structure, and enough revision information to support safe merging. It should borrow useful Git-like workflow concepts without pretending that MediaWiki is Git.
