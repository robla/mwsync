As of June 3, 2026, my design for the next generation of mwsync.py is going to be to expand my as-yet-unbuilt mwmap.py script/project.  I want a few things:
* To have the [https://github.com/robla/mwsync](https://github.com/robla/mwsync) retain its history
* To have the mysync repository have the full history of the new architecture.
* To temporarily have a separate mwmap git repository, having a time in the future where the new histories are merged.
* To have one parent directory (and "_mwsync" or whatever directory) per "wiki" or whatever.  I want subdirectories to be handled similarly to how git does it, though maybe not identically.
* To make it so that _cache and _mwsync can live together in the same directory, at least at first.  Eventually, "mwsync.py migrate" (that subcommand in the old mwsync.py repository) should be able to perform a migration from "_cache" and the top level configuration to the new configuration/cache infrastructure.