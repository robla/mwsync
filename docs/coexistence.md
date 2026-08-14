# Coexistence During Replacement

The `mwmap` branch contains two usable implementations while the imported
next-generation CLI approaches parity with `mwsync.py`. They may operate on the
same `.mw` working files, but they do not share synchronization state.

## State Ownership

| Owner | Durable configuration | Cache and operation state |
| --- | --- | --- |
| `python3 mwsync.py` | `mwsync.yaml` | `_cache/` |
| `python3 mwmap/mwmap.py` | `_mwmap/mwmap.yaml` | `_mwmap/cache/` |

Neither implementation may silently rewrite or remove the other one's config
or cache. `_mwmap/cache/` is disposable and remotely reconstructible;
`_mwmap/mwmap.yaml`, `mwsync.yaml`, and working `.mw` files are durable.
Migration may read legacy state and write corresponding `_mwmap/` state, but it
must leave `mwsync.yaml` and `_cache/` usable by the legacy command.

## Shared Working Files

The two implementations may map the same `.mw` path. An edit made through one
workflow is therefore visible to the other, but each tool retains an
independent idea of the incorporated base revision. Do not assume that a merge,
commit, pull, or push performed by one tool advances the other tool's state.

Use only one implementation at a time for a given file:

1. Finish or abandon any pending merge and pending commit in the current tool.
2. Run that tool's `status` or `fsck` and resolve unexpected state.
3. Inspect the working-file diff before switching tools.
4. Fetch and reconcile with the newly selected tool before committing or
   pushing from it.

Never run simultaneous write operations against the same working file. Until
cross-tool base-drift detection is implemented, switching tools requires manual
care; see `mwmap/tasks.org` tasks `t0003.4` and `t0004`.

## Source Authority

Development continues in this repository on the `mwmap` branch. The former
standalone checkout is retained only as an import-history reference; new
implementation commits belong in the combined repository. The legacy
`mwsync.py` executable and state remain supported until the explicit naming
cutover in `t0007`.
