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

`mwsync` syncs selected MediaWiki pages as local MediaWiki-wikitext files.

`mwmap` is intended to go further: it syncs MediaWiki content with other wiki-like formats, while preserving page identity, links, structure, and enough revision information to support safe merging.

In short:

```text
mwsync = MediaWiki ↔ local .mw files
mwmap  = MediaWiki ↔ another wiki-like system
```

## Planned command style

`mwmap` is expected to use verb-style subcommands, for example:

```sh
mwmap init
mwmap source add electowiki mediawiki https://electowiki.org/w/
mwmap source add notes zim ~/Notes/electowiki

mwmap pair page electowiki:ElectoramaNews notes:ElectoramaNews
mwmap pair subtree electowiki:ElectoramaNews/ notes:ElectoramaNews/
mwmap pair wiki electowiki notes

mwmap fetch
mwmap status
mwmap diff
mwmap merge
mwmap push
mwmap unpair
```

## Goals

* Support two-way synchronization between MediaWiki and local wiki-like formats.
* Allow users to edit content in their preferred local tools.
* Preserve links, page identity, and useful structural relationships.
* Make page, subtree, namespace, and whole-wiki mappings explicit.
* Avoid pretending that MediaWiki is Git, while still borrowing useful Git-like workflow concepts.

## Non-goals

`mwmap` is not intended to be a general MediaWiki bot framework, a one-way export tool, or a replacement for full wiki dumps. It is focused on interactive editing and synchronization between corresponding wiki-like stores.

## Status

This project is currently an idea/prototype-stage companion to `mwsync`.
