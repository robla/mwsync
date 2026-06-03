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

## Documentation

* [Architecture](docs/architecture.md)
* [Testing](docs/testing.md)
