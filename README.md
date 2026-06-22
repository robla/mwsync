# mwmap

`mwmap` is an experimental command-line tool for keeping MediaWiki content paired with other local wiki-like formats, such as Zim desktop wiki notebooks, Org-mode files, Markdown folder trees, or other structured text stores.

It is intended for workflows where MediaWiki is not merely exported once, but kept in an ongoing two-way relationship with another editable local representation.

## Concept

`mwmap` maintains mappings between MediaWiki pages, page trees, namespaces, or whole wikis and corresponding local structures.

Examples of possible mappings:

* one MediaWiki page ↔ one local file
* one MediaWiki page tree ↔ one Zim notebook subtree
* one MediaWiki namespace ↔ one local folder
* one whole MediaWiki wiki ↔ one local notebook
* one local Org-mode file ↔ many MediaWiki pages

The core abstraction is a **map**: a set of rules describing how wiki objects correspond across systems.

## Relationship to mwsync

This is still unclear.  As of June 2026, `mwsync` syncs selected MediaWiki pages as local MediaWiki-wikitext files, and maintains a 1:1 mapping between the two.  It could be that `mwmap` is really `mwsync-next-gen`.

`mwmap` will go further: it syncs MediaWiki content with other wiki-like formats, while preserving page identity, links, structure, and enough revision information to support safe merging.

That said, `mwmap` may become a plugin/extension to `mwsync`, may get rolled into `mwsync`, or may become the basis for a broader `mwsync` rearchitecture.

## Goals

* Support two-way synchronization between MediaWiki and local wiki-like formats.
* Allow users to edit content in their preferred local tools.
* Preserve links, page identity, and useful structural relationships.
* Make page, subtree, namespace, and whole-wiki mappings explicit.
* Avoid pretending that MediaWiki is Git, while still borrowing useful Git-like workflow concepts.
* Establish an architecture that may eventually make `mwmap` into "mwsync-next-generation".

## Non-goals

`mwmap` is not intended to be a general MediaWiki bot framework, a one-way export tool, or a replacement for full wiki dumps. It is focused on interactive editing and synchronization between corresponding wiki-like stores.

## Status

This project is currently an idea/prototype-stage companion to `mwsync`.

## First version target

The first version is a small Python CLI. From a source checkout, run `python3 mwmap.py ...` or put the repository root on `PATH` and use the `mwmap` wrapper. The motivating first run is onboarding a single page into an empty directory:

```sh
mwmap init
mwmap clone https://electowiki.org/wiki/California
```

The fuller command surface:

```sh
python3 mwmap.py --help
python3 mwmap.py --root ~/Notes/electowiki init
python3 mwmap.py --root ~/Notes/electowiki remote add electowiki mediawiki https://electowiki.org/w/
python3 mwmap.py --root ~/Notes/electowiki clone https://electowiki.org/wiki/California
python3 mwmap.py --root ~/Notes/electowiki clone --follow https://electowiki.org/wiki/A_Redirect
python3 mwmap.py --root ~/Notes/electowiki status
python3 mwmap.py --root ~/Notes/electowiki fetch
python3 mwmap.py --root ~/Notes/electowiki pull
python3 mwmap.py --root ~/Notes/electowiki push -m "Edit summary"
python3 mwmap.py --root ~/Notes/electowiki fsck
```

`clone` contacts MediaWiki — it registers a remote derived from the URL, pairs the page, fetches it, caches the remote's `siteinfo`, and writes the local file. If the URL names a redirect, `clone` onboards the redirect page itself and warns (matching `mwsync.py`); pass `--follow` to resolve the redirect to its target instead. `fsck` checks cache/mapping integrity.

For ongoing sync of already-paired pages, mwmap mirrors Git:

* `fetch [PATH]` downloads the latest upstream revision (by stable pageid) into the cache, touching neither the working tree nor `base_revid`.
* `merge [PATH]` three-way merges the cached upstream into each working file against its recorded `base_revid`. A clean merge advances `base_revid`; a conflict writes `<<<<<<< / ======= / >>>>>>>` markers, leaves `base_revid` unchanged, and exits nonzero. Merge refuses a file that still has unresolved markers.
* `pull [PATH]` is `fetch` then `merge`.
* `push [PATH]` uploads locally edited working files to their MediaWiki pages, guarded by `base_revid` so an upstream change since the local base is rejected as an edit conflict (resolve with `pull`, then retry). On success the new revision is re-cached and `base_revid` advances. Use `-m/--summary` for the edit summary (an editor is opened if omitted) and `--dry-run` to preview. Credentials come from the `MWMAP_MW_USER` and `MWMAP_MW_PASSWORD` environment variables (a MediaWiki bot password), never from config.

Each takes an optional local path to limit the operation to one paired page. The merge is pure-Python (no `git` binary dependency); the push login/CSRF/edit code is adapted from legacy `mwsync.py` (see [docs/legacy-code-copy.md](docs/legacy-code-copy.md)).

Required behavior:

* Accept a global `--root PATH` option, defaulting to the current directory.
* Show `init`, `clone`, `remote`, and `status` in `--help`.
* Create `_mwmap/config.yaml` and `_mwmap/cache/` on `init`.
* Store an initial config equivalent to:

```yaml
version: 1
remotes: {}
mappings: []
```

* Implement `remote add NAME TYPE LOCATION` by recording a remote in the config.
* Implement `clone URL [PATH]` for MediaWiki page URLs by registering a remote derived from the URL, pairing the page, fetching from MediaWiki, and writing the local file. It initializes the workspace if needed.
* Implement `status` by reporting configured remotes and the mapping count.
* Exit nonzero with a clear message if commands that need config run before `init`.

This drops the earlier offline-only constraint: `clone` is the first command that
contacts MediaWiki. Keep the fast local test suite offline and put `clone`'s
network behavior in a separate integration test (see [Testing](docs/testing.md)).

## Documentation

* [Architecture](docs/architecture.md)
* [Git subcommand mapping](docs/git-mapping.md)
* [Migration from mwsync](docs/migration.md)
* [Testing](docs/testing.md)
