# mwsync.py migrate

`mwsync.py migrate` brings an mwsync working directory's on-disk and
config layout forward to what current `mwsync.py` expects. It is the
companion to [`fsck`](fsck.md): `fsck` *detects* legacy-shape problems
in `mwsync.yaml` and the cache; `migrate` *fixes* them. Splitting the
roles keeps `fsck` purely diagnostic — like `git fsck` or `brew doctor`
— and confines working-tree changes to a command whose name says what
it does.

The rationale for choosing migration over permanent compatibility shims
is in [namespaces.md](namespaces.md) (Migration via mwsync.py migrate).
In short: `mwsync` is new enough that compat code costs more to maintain
than asking users to run `migrate` once.

## Usage

```bash
python3 mwsync.py migrate                # migrate every legacy entry
python3 mwsync.py migrate New_York       # migrate one article
python3 mwsync.py migrate --dry-run      # preview without writing
python3 mwsync.py migrate --yes          # auto-accept risky prompts
```

`migrate` resolves the optional `ARTICLE` argument the same way every
other subcommand does (article key or local filename).

When another `mwsync.py` command refuses to operate on a working
directory because it detects a legacy shape, the user-facing error
message should name `mwsync.py migrate` as the fix. `migrate` itself is
the only command expected to do those fixes; ad-hoc compat code
elsewhere in the codebase is discouraged.

## Migration classes

Migrations are split by risk.

### Safe migrations (applied without prompting)

Pure metadata additions that do not touch files on disk:

- Add `namespace`, `namespace_name`, and `dbkey` to entries whose `title`
  resolves cleanly through the cached namespace map (see
  [namespaces.md](namespaces.md) — *Namespace Metadata Source*).
  Main-namespace pages stay omitted, per the spec.

One line per migrated entry is printed:

```
<key>: added namespace metadata (namespace=<id>, dbkey=<dbkey>)
```

### Risky migrations (per-entry prompt; `--yes` skips prompt)

These move or rename files the user might be editing or that contain
checked-out state:

- Rename a literal-colon article key (e.g. `Talk:Software` →
  `Talk__Software`) under `wiki.articles`, and rename the corresponding
  cache directory (`_cache/Talk:Software/` → `_cache/Talk__Software/`)
  to match.
- Move a flat working file (`Talk__Software.mw`) into its
  namespace-aware location (`Talk/Software.mw`), creating intermediate
  directories as needed, and update the entry's `local` field. The
  article key does not change in this migration.

Each prompt prints the full set of changes for one entry and asks for
`y`/`N`:

```
<key>: ready to migrate this entry:
  rename key:  Talk:Software -> Talk__Software
  move file:   Talk:Software.mw -> Talk/Software.mw
  move cache:  _cache/Talk:Software/ -> _cache/Talk__Software/
Apply? [y/N]
```

Anything other than `y`/`yes` (or EOF / non-TTY without `--yes`) skips
that entry and moves on; the entry remains legacy until a future run.

## Atomicity

`mwsync.yaml` is updated atomically (via `_atomic_write` /
`save_config`) only after the on-disk file and directory renames for
that entry succeed. If a rename fails midway, `migrate` reports the
partial state and exits non-zero without saving the config — the user
is responsible for either retrying or restoring by hand. Under
`--dry-run` nothing is written at all, so this failure mode does not
arise.

## Out of scope

`migrate` does **not** migrate `_cache/server--<key>.mw` snapshots to
the new per-article cache layout. That format predates the current
cache design by enough that the working-copy state cannot be
reconstructed reliably from the snapshot alone (no history, no refs,
no per-revision metadata). The legacy-snapshot detection in `fsck`
stays "report and tell the user to re-fetch", per
[legacy.md](legacy.md).

## Exit status

- `0` — every legacy entry that needed migrating was migrated, or
  nothing needed migrating.
- `1` — at least one entry could not be migrated: a risky migration
  the user declined, a failure during file/directory rename, or a
  cache-integrity issue that should be resolved by `fsck` first.

## Relationship to fsck and the resolver

`fsck` reports legacy entries but never fixes them. `migrate` is the
only command that rewrites `mwsync.yaml` entries or moves working
files into their namespace-aware locations.

The Lookup Resolution Algorithm in [namespaces.md](namespaces.md)
keeps a transitional fallback so commands continue to work in a
working directory that has not been migrated yet. `migrate` is the
mechanism that removes the need for that fallback in a given working
directory. The fallback may be dropped in a later release once
`migrate` is in wide use; new features should not be designed assuming
the legacy shape keeps working.
