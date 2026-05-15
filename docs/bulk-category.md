# Bulk Category Mapping Specification (Editor Pattern)

This document describes the "Editor Pattern" for batch-resolving unknown
categories during a `ledecopy.py` import. It is inspired by `git rebase -i`
and provides a way to handle dozens of category decisions in a single
interaction without external TUI dependencies.

## Workflow

When `ledecopy.py` identifies categories that are not already resolved by
`catmap.yaml` or an implicit cache hit, and the session is interactive:

1.  **Generate Buffer:** Create a temporary text file containing all unknown
    categories, each prefixed with a default action marker (usually `?`).
2.  **Open Editor:** Launch the user's preferred editor (`$VISUAL`, `$EDITOR`,
    or a fallback like `nano` or `vi`) on the temporary file.
3.  **User Edit:** The user changes the action markers and/or provides
    mapping targets.
4.  **Parse & Apply:** Upon editor save and exit, `ledecopy.py` parses the
    buffer and applies the decisions to both the current draft and
    `catmap.yaml`.

## Buffer Format

The buffer is a plain text file using a "command-first" layout. Lines
starting with `#` are ignored as comments.

```text
# ledecopy.py Category Mapping: "New York"
#
# Commands:
#  k, keep          - Keep as-is and save to catmap.yaml
#  d, drop          - Drop (omit) and save to catmap.yaml
#  m, map <Target>  - Map to Electowiki category <Target> and save
#  s, skip          - Skip for this run (do not save to catmap.yaml)
#
# If you leave a category with '?', it will be skipped for this run.
# If you delete a line, that category will be skipped for this run.
# --------------------------------------------------------------------------

k  United States elections
d  Redirects from New York
m  Legislatures | State legislatures of the United States
?  Ranked voting methods
```

## Action Protocols

Each non-comment line must start with a command character or keyword,
followed by the category name.

| Command | Keywords | Meaning | Resulting `catmap.yaml` |
| :--- | :--- | :--- | :--- |
| `k` | `keep` | Keep unchanged | `Source: Source` |
| `d` | `drop` | Drop from draft | `Source: null` |
| `m` | `map` | Rename to `<Target>` | `Source: Target` |
| `s` | `skip` | Use once (no save) | *(No entry)* |
| `?` | *(none)* | Undecided | *(No entry)* |

### Mapping Syntax (`m`)

The mapping command requires a delimiter to separate the new target from
the original name.

Format: `m <Target> | <Original Name>`

Using `|` (pipe) as a delimiter is recommended as it is invalid in
MediaWiki page titles, ensuring unambiguous parsing.

## Editor Invocation

1.  **Selection:** Use `os.environ.get('VISUAL')` first, then
    `os.environ.get('EDITOR')`, then fallback to `nano` (or `vi` on systems
    where `nano` is unlikely).
2.  **Atomic Temp File:** Create the file in the working directory (or system
    temp) with a name like `.ledecopy-batch.txt`.
3.  **Subprocess:** Use `subprocess.call` to wait for the editor to finish.

## Parsing Logic

- **Normalization:** The parser must normalize the `Original Name` found in
  the file back to the source list to ensure the user hasn't accidentally
  typoed the source name.
- **Errors:** If a line is malformed (e.g. `m` without a `|`),
  `ledecopy.py` should:
    1.  Print a warning to stderr.
    2.  Report which lines failed.
    3.  Ask the user if they want to **(e)dit again** or **(c)ontinue** (skipping
        invalid lines).
- **Persistence:** Decisions marked for "save" (`k`, `d`, `m`) are appended
  to `catmap.yaml` using the same atomic-write helper used by `mwsync.py`.

## Advantages

- **YAGNI:** Zero external library dependencies.
- **Efficiency:** Users can use editor features (macros, find/replace,
  multi-cursors) to map bulk imports.
- **Clarity:** The buffer provides a "birds-eye view" of all categories
  before a single one is committed to `catmap.yaml`.
- **Git-like:** Familiar to any developer comfortable with `git commit --amend`
  or `git rebase`.
