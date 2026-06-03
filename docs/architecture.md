# Architecture

`mwmap` is still in the idea/prototype stage, so this document describes direction rather than a committed implementation.

## Relationship to mwsync

`mwsync.py` is a rather opaque monolith. `mwmap` should avoid repeating that shape if it becomes more than a small experiment.

The relationship between `mwmap` and `mwsync` is still unresolved. `mwmap` may become a plugin or extension to `mwsync`, may be rolled into `mwsync`, or may become the basis for a broader `mwsync` rearchitecture. If `mwmap` is successful, it may effectively become "mwsync 2.0".

Design choices should preserve that flexibility. Avoid command names, data models, or package boundaries that would make future integration with `mwsync` unnecessarily awkward.

## Planned Command Style

`mwmap` is expected to use verb-style subcommands:

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

These verbs may eventually need to become `mwsync` verbs. For example, `mwmap init` might become `mwsync.py mapinit`, and `mwmap push` might become `mwsync push --full`. Choose verbs with that migration path in mind.

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
      source.py
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
