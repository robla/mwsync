# Extras

This directory contains optional integration files for local development and
interactive use. Nothing here is required by the core Python scripts.

## Bash Completion

`mwsync.bash` provides Bash tab completion for `mwsync.py` and the supported
short alias `mws`. It completes subcommands, common options, registered article
keys, and configured local `.mw` filenames from the current directory's
`mwsync.yaml`. It only reads local files and never calls the MediaWiki API.

To enable it for the current shell:

```bash
source /path/to/mwsync/extras/mwsync.bash
```

To enable it automatically, add that `source` line to `~/.bashrc`.

If `mwsync.py` is already on your `PATH`, the recommended short alias is:

```bash
alias mws=mwsync.py
```

Define the alias before or after sourcing `mwsync.bash`; the completion script
registers both command names.
