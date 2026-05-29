# File And Image Checkout

`mwsync.py checkout File:Example.png` should fetch both parts of a MediaWiki
file page:

- the File namespace page wikitext, such as description, license, and
  categories;
- the current uploaded binary file, such as the PNG, JPG, SVG, or PDF payload.

The wikitext remains the tracked mwsync page. The binary payload is companion
state, not a replacement for the `.mw` working file.

## Desired Workflow

```bash
mwsync.py checkout File:YeeBellCurveDiagram2010.png
```

Expected local result:

```text
06ns_File/YeeBellCurveDiagram2010.png.mw
06ns_File/YeeBellCurveDiagram2010.png
_cache/File__YeeBellCurveDiagram2010.png/
```

The `.mw` file is editable wikitext. The adjacent file without `.mw` is the
downloaded media payload. Keeping them next to each other makes shell browsing
simple and preserves the visible relationship between metadata and media.

## Config Shape

File entries should continue to live under `wiki.articles`:

```yaml
wiki:
  articles:
    File__YeeBellCurveDiagram2010.png:
      title: File:YeeBellCurveDiagram2010.png
      namespace: 6
      namespace_name: File
      dbkey: YeeBellCurveDiagram2010.png
      local: 06ns_File/YeeBellCurveDiagram2010.png.mw
      media_local: 06ns_File/YeeBellCurveDiagram2010.png
```

`local` is always the wikitext working file. `media_local` is the current
downloaded file payload. Main page sync logic should not infer the media path
by stripping `.mw`; store it explicitly for clarity and future migrations.

## Cache Layout

The per-page cache should gain media files alongside revision cache files:

```text
_cache/File__YeeBellCurveDiagram2010.png/
  history.jsonl
  refs/upstream
  refs/base
  <revid>.mw
  <revid>.json
  media/
    current.bin
    current.json
    sha1/<sha1>.bin
    sha1/<sha1>.json
```

The `sha1/<sha1>.*` files are immutable-ish content snapshots keyed by the
MediaWiki file SHA-1 from `imageinfo`. `media/current.*` points at the current
payload metadata and gives simple commands a stable place to read from. If the
same file SHA-1 is fetched again, reuse the existing cached body.

## API Calls

Use the Action API, matching the rest of `mwsync.py`:

```text
action=query
titles=File:YeeBellCurveDiagram2010.png
prop=revisions|imageinfo
rvprop=content|ids|timestamp|user|comment|sha1|size
iiprop=url|size|sha1|mime|mediatype|metadata|timestamp|user|comment
format=json
formatversion=2
```

Then download the binary from `imageinfo[0].url` using `urllib.request` and the
shared `USER_AGENT`. Do not scrape the file description page HTML.

## Command Behavior

`checkout File:...` should register the File page, fetch the wikitext revision,
download the current media payload, write both local files atomically, and then
save `mwsync.yaml`.

`fetch File:...` should update cache state for both the File page revision and
the current media payload, but should not overwrite either local working file.

`merge File:...` should update the `.mw` wikitext from `refs/upstream` as usual.
For the media payload, the initial rule should be simple: if `media_local` is
missing or still byte-identical to the previous cached media payload, replace it
with the newly fetched payload; otherwise leave it untouched and report a local
media modification.

`status` should report media state for File namespace entries:

```text
modified-media     File__Example.png  06ns_File/Example.png
missing-media      File__Example.png  06ns_File/Example.png
```

## Push Scope

Do not implement media upload as part of this first step. `push` should continue
to push only the File page wikitext. If `media_local` differs from cached media,
`push` should warn that the media payload is modified locally and is not being
uploaded.

Future media upload support needs a separate design because MediaWiki upload
uses different API parameters, upload tokens, filename conflict rules, and
licensing implications.

## Error Handling

If a File page has no `imageinfo` entry, still checkout the `.mw` wikitext and
print a warning that no current media payload was available. This can happen for
broken file pages or deleted/missing uploads.

If the media download fails after wikitext fetch succeeds, the command should
fail before claiming checkout success. For a new checkout, clean up files
created in that transaction when practical.

If the local media path already exists for a new checkout, fail before writing
unless a future explicit overwrite option is added.

## Future Directions

- `mwsync.py fetch --media-only File:Example.png`
- `mwsync.py diff --media File:Example.png`, comparing hashes and metadata
- thumbnail download using `iiurlwidth` / `iiurlheight`
- media upload with explicit `mwsync.py upload` or `mwsync.py push --media`
- support for historical file revisions via `iilimit`, `iistart`, and `iiend`
