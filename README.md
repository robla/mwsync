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

This is still unclear.  As of June 2026, `mwsync` syncs selected MediaWiki pages as local MediaWiki-wikitext files, and maintains a 1:1 mapping between the two.

`mwmap` will go further: it syncs MediaWiki content with other wiki-like formats, while preserving page identity, links, structure, and enough revision information to support safe merging.

That said, `mwmap` may become a plugin/extension to `mwsync`, and/or may get rolled into `mwsync`.  Or we may want to start off with `mwmap` being part of `mwsync`, using this as an opportunity for a broader rearchitecture of `mwsync`.  TBD.

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

However, in designing `mwmap`, we should bear in mind that we may want to turn all new `mwmap` verbs into `mwsync` verbs.  "mwmap init" may become "mwsync.py mapinit".  We should avoid reusing verbs that won't be easy to merge into `mwsync`'s verb set, though that may be easy enough to mitigate with switches (e.g. `mwmap push` may become `mwsync push --full`)

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
