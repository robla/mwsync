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

The first version of `mwmap.py` should be a small Python CLI runnable from the repository root. The motivating first run is onboarding a single page into an empty directory:

```sh
python3 mwmap.py init
python3 mwmap.py clone https://electowiki.org/wiki/California
```

The fuller command surface:

```sh
python3 mwmap.py --help
python3 mwmap.py --root ~/Notes/electowiki init
python3 mwmap.py --root ~/Notes/electowiki remote add electowiki mediawiki https://electowiki.org/w/
python3 mwmap.py --root ~/Notes/electowiki clone https://electowiki.org/wiki/California
python3 mwmap.py --root ~/Notes/electowiki status
```

`clone` contacts MediaWiki — it registers a remote derived from the URL, pairs the page, fetches it, and writes the local file. The other commands operate on local mapping metadata.

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
* Implement `clone URL [PATH]` by onboarding a page (or wiki) end to end: registering a remote derived from the URL, pairing, fetching from MediaWiki, and writing local files. It initializes the workspace if needed.
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
