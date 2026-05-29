# File And Image Checkout

`mwsync.py` should treat a MediaWiki `File:` page as two related objects:

- the File namespace wikitext page, which is versioned by page revision ID;
- the uploaded media payload, which has its own upload history.

The `.mw` file remains the normal editable working file. Media payloads are
optional companion files. Do not download large binary files by default unless
the user explicitly asks or confirms an interactive prompt.

## Desired Workflow

```bash
mwsync.py checkout File:YeeBellCurveDiagram2010.png
```

Initial behavior should fetch and merge the File page wikitext. For the media
payload, ask interactively:

```text
Download current media file too? [y/N]
```

Non-interactive runs should default to no media download. Explicit options can
avoid the prompt:

```bash
mwsync.py checkout File:YeeBellCurveDiagram2010.png --media
mwsync.py checkout File:YeeBellCurveDiagram2010.png --no-media
mwsync.py fetch File:YeeBellCurveDiagram2010.png --media
```

## Visible Layout

For a downloaded media file, the visible checkout should place the payload next
to the wikitext:

```text
06ns_File/YeeBellCurveDiagram2010.png.mw
06ns_File/YeeBellCurveDiagram2010.png
```

The visible media file should be a hard link to the cached media payload when
possible. This keeps the file easy to inspect while avoiding an extra copy in
the common Linux/Crostini case. If hard-link creation fails, the command should
fail gracefully rather than silently switching storage models during early
development.

Hard links are an implementation choice, not a cross-platform guarantee. They
require the cache and visible checkout to live on the same filesystem and may
not work on every Windows, macOS, network, or sync-backed filesystem.

## Config Shape

File entries should continue to live under `wiki.articles`:

```yaml
wiki:
  articles:
    File__YeeBellCurveDiagram2010.png:
      title: File:YeeBellCurveDiagram2010.png
      namespace: 6
      namespace_name: File
      page_dbkey: YeeBellCurveDiagram2010.png
      local: 06ns_File/YeeBellCurveDiagram2010.png.mw
      media_local: 06ns_File/YeeBellCurveDiagram2010.png
```

`local` is always the wikitext working file. `media_local` is the visible media
payload path, if the user has downloaded one.

## Cache Layout

Keep media state inside the same per-page cache as the File page wikitext:

```text
_cache/File__YeeBellCurveDiagram2010.png/
  history.jsonl
  refs/upstream
  refs/base
  <revid>.mw
  <revid>.json
  media/
    manifest.jsonl
    refs/current
    images/f/f8/YeeBellCurveDiagram2010.png
    archive/f/f8/20250101000000!YeeBellCurveDiagram2010.png
```

Use MediaWiki-style hashed paths for media payloads, not a new content-addressed
scheme. MediaWiki still defaults to `$wgHashedUploadDirectory = true`, using
the MD5 of the filename: first hex digit for the first directory and first two
hex digits for the second directory.

The cache should mirror MediaWiki naming closely:

- current file: `media/images/<md5[0]>/<md5[0:2]>/<filename>`;
- old upload: `media/archive/<md5[0]>/<md5[0:2]>/<timestamp>!<filename>`;
- metadata: append `.json` beside the cached payload or record it in
  `media/manifest.jsonl`.

`media/manifest.jsonl` should record every known upload version in chronological
order when available. Each row should include filename, archive name when
present, timestamp, user, comment, size, width, height, MIME type, SHA-1 from
MediaWiki, source URL, local cache path, and whether the payload was downloaded.

## API Calls

Use the Action API, matching the rest of `mwsync.py`:

```text
action=query
titles=File:YeeBellCurveDiagram2010.png
prop=revisions|imageinfo
rvprop=content|ids|timestamp|user|comment|sha1|size
iiprop=url|size|sha1|mime|mediatype|metadata|timestamp|user|comment|archivename
iiurlwidth=
format=json
formatversion=2
```

Then download the binary from `imageinfo[0].url` only when media download was
requested or confirmed. Do not scrape the file description page HTML.

## Command Behavior

`checkout File:...` should register the File page, fetch the wikitext revision,
and write the `.mw` working file. It should download and hard-link the current
media payload only when requested or confirmed.

`fetch File:...` should update cache metadata and the File page revision cache.
It should not overwrite the `.mw` working file or visible media file. With
`--media`, it should also download the current payload and update
`media/refs/current`.

`merge File:...` should update the `.mw` wikitext from `refs/upstream` as usual.
It should not change the visible media file unless media behavior is explicitly
requested in a later design.

`status` should report media state for File namespace entries when a media file
is configured:

```text
modified-media     File__Example.png  06ns_File/Example.png
missing-media      File__Example.png  06ns_File/Example.png
```

## Push Scope

Do not implement media upload as part of the first media-cache step. `push`
should continue to push only File page wikitext. If `media_local` differs from
the cached media payload, `push` should warn that the media payload is modified
locally and is not being uploaded.

Future media upload support needs a separate design because MediaWiki upload
uses different API parameters, upload tokens, filename conflict rules, and
licensing implications.

## Error Handling

If a File page has no `imageinfo` entry, still checkout the `.mw` wikitext and
print a warning that no current media payload was available. This can happen for
broken file pages or deleted/missing uploads.

If the media download fails after wikitext fetch succeeds, keep the wikitext
checkout and report that the media payload was not downloaded.

If hard-link creation fails, report the cache path and visible path and leave
the cached payload in place. Do not silently copy unless a future explicit
fallback option is added.

If the local media path already exists for a new checkout, fail before writing
unless an explicit overwrite option is added.

## Future Directions

- `mwsync.py fetch --media-history File:Example.png`
- `mwsync.py diff --media File:Example.png`, comparing hashes and metadata
- thumbnail download using `iiurlwidth` / `iiurlheight`
- media upload with explicit `mwsync.py upload` or `mwsync.py push --media`
- optional copy fallback for filesystems that cannot hard-link
