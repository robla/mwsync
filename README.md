`mwsync` is a command-line tool bringing git-like workflow to wiki
page editing (on MediaWiki-based wikis).  It allows for editing
selected MediaWiki pages locally by creating a working copy and a
sparse cache of MediaWiki metadata and pages, and allows users to view
page logs from local cache, store local copies of server page
revisions, and push local changes back to the server.  It was mostly
vibecoded with a combination of OpenAI's ChatGPT, Anthropic's Claude,
and Google's Gemini (more the former than the latter).

Though `mwsync` is heavily inspired by `git` (having subcommands with
similar semantics like "checkout", "fetch", "diff", "merge", "push"),
it currently does not have a git-compatible backend.  It instead
relies on having a "_cache" subdirectory where some MediaWiki metadata
and revisions are stored.  This helps the tool provide an inspectable
workflow to edit wiki pages in one's normal local editor, caching
enough to allow accurate page diffs and other change inspection prior
to pushing changes back through the MediaWiki API.

`mwsync` is released under the MIT license; see [LICENSE](LICENSE).
Contributions are welcome; see [CONTRIBUTORS](CONTRIBUTORS) and
[docs/contributor-guide.md](docs/contributor-guide.md) for more.

## Relationship to other MediaWiki synchronization tools

`mwsync` is far from the only tool in this problem space.
Similar(-ish) projects include:

*
  [`git-mediawiki`](https://www.mediawiki.org/wiki/Git-remote-mediawiki)
  A Perl-based `git` extension that lets users read and write
  MediaWiki pages as though the wiki were a `git` remote. `mwsync`
  takes a less `git`-internal approach: it uses MediaWiki revision IDs
  and local files directly, rather than representing wiki history as
  `git` refs and commits.
*
  [`mvs`](https://metacpan.org/dist/WWW-Mediawiki-Client/view/bin/mvs):
  an Perl-based command-line MediaWiki client first published in 2004
  for offline page editing. Like `mwsync`, it supports editing wiki
  pages with a local editor and merging concurrent changes.  Unlike
  `mwsync`, it was inspired by older version-control systems like CVS
  and Subversion.
*
  [`Pywikibot`](https://www.mediawiki.org/wiki/Manual:Pywikibot/Overview)
  and [`mwclient`](https://mwclient.readthedocs.io/): mature Python
  libraries for automating MediaWiki edits. These are good foundations
  for bots and scripts, but they are not primarily local working-copy
  tools. `mwsync` is aimed more at interactive editing than bot
  automation.
* [`PageSync`](https://www.mediawiki.org/wiki/Extension:PageSync): a
  MediaWiki extension for storing wiki page content as files on the
  server, so it can be versioned and deployed. By contrast, `mwsync`
  is a client-side tool and does not require installing a MediaWiki
  extension or controlling the wiki server.
*
  [`Local-MediaWiki-Sync`](https://github.com/aaronpk/Local-MediaWiki-Sync):
  A PHP-based tool for archiving or mirroring whole wikis as local
  files. `mwsync` has a narrower focus on selected pages and an
  edit/merge/push workflow rather than complete archival mirroring.

All of the tools above are much more mature than `mwsync`, and (as of
this writing in May 2026) are probably much better candidates for
production use.
