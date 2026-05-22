# mwsync.py fsck

`mwsync.py fsck` checks that the on-disk state of an mwsync working
directory is internally consistent. It is patterned loosely on `git fsck`:
it does not talk to the network, it does not change the working copy, and
it is safe to run at any time. It is the first stop when an `mwsync.py`
command behaves unexpectedly. Fixing what `fsck` reports — including
the legacy-shape problems added in the namespace work — is the job of
[`mwsync.py migrate`](migrate.md), not `fsck` itself.

This document describes both the current behavior and the planned
extensions. The "Current Behavior" section corresponds to what is in
`mwsync.py` today (`run_fsck` / `_fsck_article`); the "After
namespaces.md" section describes the additional detections needed to
land that spec.

## Usage

```bash
python3 mwsync.py fsck                  # check every registered article
python3 mwsync.py fsck New_York         # check one article
```

Exit status:

- `0` — no issues found across the articles checked.
- `1` — at least one issue found; details printed to stdout, summary to
  stderr.

`fsck` resolves the optional `ARTICLE` argument through the same
`resolve_article_entry` path as the rest of the CLI, so it accepts an
article key (`New_York`) or a local filename (`New_York.mw`).

## Current Behavior

For each article in `wiki.articles`, `fsck` checks:

### Legacy cache snapshot

If the per-article directory `_cache/<Article_Key>/` does not exist but
the older flat snapshot `_cache/server--<Article_Key>.mw` does, fsck
reports:

```
<key>: legacy cache detected: _cache/server--<key>.mw
```

This is the same condition that aborts most other commands via
`_check_legacy_cache`. Today fsck only reports it — there is no migration
path in the tool; users are told to delete the snapshot and re-fetch (or
run an external migration tool). See [legacy.md](legacy.md) for the older
format.

### History ledger (`_cache/<key>/history.jsonl`)

For each entry in the ledger:

- `revid` must parse as an integer; otherwise reported as
  `invalid history revid: ...`.
- `revid` must be unique within the file; duplicates are reported as
  `duplicate history revid: <revid>`.
- Entries must be chronologically ordered by `(timestamp, revid)` —
  oldest first. A regression is reported as
  `history is not chronological near revid <revid>`.

### Per-revision metadata sidecars

For every entry with a `meta` field:

- The sidecar file (`<revid>.json`) must exist; otherwise
  `missing metadata sidecar for revid <revid>: <path>`.
- The sidecar JSON must parse; load failure is reported as
  `cannot read metadata sidecar <path>: <err>`.
- `meta.revid` must match the ledger entry's `revid`, else
  `metadata revid mismatch in <path>`.
- If both the ledger and the sidecar carry `sha1`, they must agree;
  mismatch is reported as `sha1 mismatch between history and <path>`.

### Per-revision bodies

For every entry with a `body` field, the body file (`<revid>.mw`) must
exist; missing files are reported as
`missing cached body for revid <revid>: <path>`.

### Refs (`_cache/<key>/refs/{upstream,base,last-pushed}`)

For each ref file that exists:

- Its contents must parse as an integer revid; non-integer contents are
  reported as `invalid refs/<ref>: <err>`.
- If history is non-empty, the ref must point at a revid present in
  history; outside-of-history refs are reported as
  `refs/<ref> points outside history: <revid>`.
- For `upstream` and `base`, the corresponding body file must exist on
  disk (because sync code needs to read it); missing bodies are reported
  as `refs/<ref> body is missing: <path>`.

### Upstream / history-tip agreement

If history is non-empty and `refs/upstream` exists, the ref must equal
the latest history revid. Mismatch is reported as
`refs/upstream (<ref>) does not match latest history (<tip>)`.

### Summary

A clean article prints `<key>: ok`. After all articles are checked, if
any issues were found, a final line goes to stderr:

```
fsck found <N> issue(s).
```

and the process exits 1.

## After namespaces.md

Once the namespace handling in [namespaces.md](namespaces.md) lands,
`fsck` gains new detections for the article-entry schema. It stays
purely diagnostic — like `git fsck` or `brew doctor` — and the actual
fixing is handled by a separate command,
[`mwsync.py migrate`](migrate.md). The existing cache-integrity checks
above are unchanged, as is the CLI surface (no new flags on `fsck`).

### New detections

For each registered article, fsck additionally reports:

- **Missing namespace metadata** — entry appears to be a non-main-namespace
  page but lacks `namespace`, `dbkey`, or `namespace_name`. An entry appears
  non-main when its `title` parses with a recognized non-main namespace prefix,
  or when a colon-bearing key parses that way. Main-namespace entries may omit
  these fields; `fsck` should derive `namespace: 0` and `dbkey` from `title`
  the same way the resolver does. Non-main omissions are reported as
  `<key>: missing namespace metadata (title=<title>)`.
- **Literal-colon key** — entry's key in `wiki.articles` contains `:`
  (e.g. an old `Talk:Software` registration). Reported as
  `<key>: legacy colon-bearing key, would migrate to <new_key>`.
- **Flat non-main local path** — entry is a non-main-namespace article
  whose `local` is a flat filename rather than `<Namespace>/<DBKey>.mw`.
  Reported as
  `<key>: flat local path for non-main namespace: <local>`.

These reports contribute to the same issue count as the integrity
checks, so legacy-shape working directories exit `1` and trip automation
the same way a missing body or a bad ref does. The remediation in the
message points at `mwsync.py migrate` rather than telling the user to
edit `mwsync.yaml` by hand.

### Legacy cache snapshot — still report-only

The existing `legacy cache detected: _cache/server--<key>.mw` report is
unchanged. `mwsync.py migrate` does not attempt to convert these
snapshots to the per-article cache layout (no history, no refs, no
per-revision metadata to reconstruct from); the message continues to
ask the user to delete and re-fetch. See [legacy.md](legacy.md).

### Interaction with the resolver fallback

The Lookup Resolution Algorithm in [namespaces.md](namespaces.md) keeps
a transitional fallback to `title` comparison for older non-main entries
without `namespace`/`dbkey`. `mwsync.py migrate` is the mechanism that removes
the need for that fallback in a given working directory. New features
should not rely on the non-main fallback continuing to exist; it may be dropped
in a later release once `migrate` is in wide use. Deriving main-namespace
metadata from `title` remains normal behavior.
