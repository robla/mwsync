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
  mwmap.yaml
  cache/
```

`_mwmap/mwmap.yaml` is durable user-facing map configuration. It should store remote definitions, mapping rules, and other state that defines the user's intended relationship between systems. `_mwmap/config.yaml` was the prototype name and should be read as a legacy fallback during migration.

`_mwmap/cache/` is disposable storage. It may hold remote-derived metadata, fetched page bodies, local-store indexes, or other data that can be repopulated from remotes if deleted.

Fetched MediaWiki page bodies should be cached under revision-stable names, not as `latest` snapshots. Pages are keyed by their stable MediaWiki `pageid`, not their (movable) title, so renaming/moving a page on the wiki does not orphan its cached history. The page cache layout is:

```text
_mwmap/cache/<remote>/
  site.yaml
  pages/
    <pageid>/
      page.yaml
      history.jsonl
      <revid>.mw
      <revid>.yaml
      <title-key>.mw -> <revid>.mw
  by-title/
    <namespace-dir>/
      <title-key> -> ../../pages/<pageid>
```

`site.yaml` caches remote-wide metadata (server, scriptpath, articlepath, and the namespace table) fetched once per remote via `meta=siteinfo`. It is the basis for future link rewriting and robust title↔URL mapping; its fetch is non-fatal (a failure only warns).

`page.yaml` is a readable directory marker recording the current title, namespace, title key, and current/base revid, so a numeric `pageid` directory is identifiable at a glance. `history.jsonl` is the per-page revision ledger; each record carries the title *as of that revision*, so a page move shows up as a title change across records. The revid-named `.mw` file is the cached body for that exact MediaWiki revision, and the matching `.yaml` file is its metadata sidecar.

Because these per-page files are written atomically one at a time but not as a set, a crash mid-fetch can leave a partial revision (a body without its sidecar/history, or vice versa). `mwmap fsck` checks for that rather than relying on locking.

### Readable cache aliases

The pageid-keyed cache is stable but hard to inspect with `ls`. `mwmap` keeps pageid as the canonical storage key while adding disposable, namespace-aware aliases for human navigation:

```text
_mwmap/cache/electowiki/
  site.yaml
  pages/
    2598/
      page.yaml
      history.jsonl
      16692.mw
      16692.yaml
      California.mw -> 16692.mw
  by-title/
    00ns_main/
      California -> ../../pages/2598
    10ns_Template/
      Template__ElectoramaNews-sidebar -> ../../pages/1234
    14ns_Category/
      Category__Voting -> ../../pages/5678
```

`pages/<pageid>/` remains canonical. `by-title/` is a rebuildable index, ideally symlinks where the platform supports them and small YAML pointer files otherwise. The namespace directory combines namespace id and canonical namespace name so related pages sort together and remain understandable without opening `site.yaml`.

The per-page `California.mw -> 16692.mw` alias is also rebuildable. It should point at the current cached body used as the local file's base revision, not replace the revid-named body. This keeps revision files static while making the most common debug action (`less .../California.mw`) obvious.

`page.yaml` should carry the fields needed to rebuild these aliases, for example:

```yaml
pageid: 2598
namespace: 0
title: California
title_key: California
current_revid: 16692
base_revid: 16692
```

For namespace pages, `title_key` should follow the readable `mwsync` convention (`Category__Voting`, `Template__ElectoramaNews-sidebar`) while namespace directory names use the more explicit `14ns_Category` form. Namespace handling should be driven by cached siteinfo and MediaWiki page metadata, not string guessing.

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
mwmap preview
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
stored under `remotes:` in `mwmap.yaml`. There can be many remotes (e.g. two
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
remotes:
  electowiki:
    type: mediawiki
    location: https://electowiki.org/w/
default_remote: electowiki
mappings:
  - type: page
    remote: electowiki
    pageid: 4242
    format: mw
    remote_path: ElectoramaNews
    local_path: ElectoramaNews.mw
    base_revid: 99
```

A mapping's identity is `(remote, pageid)`. `remote_path` (the title) is a
refreshable human label, updated on each fetch; `pageid` is the stable key.
`format` names the local representation (`mw` raw wikitext today; Org/Markdown/
Zim later) — the seam where conversion plugs in. `base_revid` records which
cached revision the working file was derived from: the merge-base that `diff`,
`merge`, and `push` need to act safely.

The default `local_path` for a cloned page is derived mwsync-style
(`workspace.local_path_for_page`): a main-namespace page is `<dbkey>.mw` at the
root, while a page in another namespace lives under a `NNns_Name/` directory
(e.g. `User:RobLa` → `02ns_User/RobLa.mw`), with subpage slashes escaped to
`__`. A literal `:` or `/` is never written into a working filename — that
would break `rsync`/`scp` `host:path` parsing, Windows filenames, and Zim's
`:` page separator. `pageid` remains the identity, so `local_path` is only a
human label; an explicit `clone URL PATH` overrides the derived name.

Keep YAGNI in mind: the first milestone covers `init`, page-oriented `clone`,
`remote add`, and `status` — so it does fetch content, via `clone`. With a
single remote (the common first run) the default is simply that sole remote. A
settable `default_remote` pointer for *multiple* remotes, richer per-pairing
upstream management, and subtree/wiki clone remain design direction — not code
to build yet.

For the newer direction where one local file can track multiple upstream pages,
see [Multi-upstream design](multi-upstream.md). That design removes any
load-bearing config version number, moves upstream identity and `base_revid`
under per-mapping `upstreams:` entries, and keeps `remotes:` global and the
cache keyed by `<remote>/pages/<pageid>/`.

## Source Layout

The implementation now uses a small package layout while keeping root-level
entry points for source-checkout use:

```text
mwmap
mwmap.py
src/
  mwmap/
    __init__.py
    cli.py
    workspace.py
    sync.py
    commands/
      __init__.py
      clone.py
      fsck.py
      init.py
      remote.py
      status.py
    core/
      __init__.py
      mediawiki.py
      remote.py
      misc.py
```

`workspace.py` owns pairing/config and cache layout; `sync.py` owns content
movement (remote → cache → working tree) and is the separation the design
calls for between *configuration* and *synchronization*. `core/remote.py` is
the single seam where a remote backend (MediaWiki today) plugs in: commands and
`sync.py` talk to a `Remote` protocol, never to a backend directly.

The typical local-command call stack is:

```text
mwmap.py
  -> mwmap.cli.main()
  -> mwmap.cli.build_cli_parser()
  -> mwmap.commands.<verb>.run_*(args)
  -> mwmap.workspace and mwmap.core helpers
```

The root `mwmap.py` only adds `src/` to `sys.path` and calls `mwmap.cli.main()`.
The `mwmap` shell wrapper calls `python3 mwmap.py`. Command modules should stay
small and delegate workspace state to `workspace.py` and low-level helpers to
`core/`.

## Core Design Direction

The central abstraction is a map: a set of rules describing how MediaWiki pages, page trees, namespaces, or whole wikis correspond to local structures.

The implementation should preserve page identity, links, structure, and enough revision information to support safe merging. It should borrow useful Git-like workflow concepts without pretending that MediaWiki is Git.
