ledecopy.py is a tool that copies ONLY THE LEDE from an enwiki
article, and then creates an mwsync-compatible local copy of an
article ready to push to electowiki.  It does not require
authentication on either enwiki nor on electowiki.  It puts a pointer to enwiki at the top of the new article (using
https://electowiki.org/wiki/Template:Wikipedia) and at the bottom of the article (using
https://electowiki.org/wiki/Template:Fromwikipedia), and notes the
exact revid from enwiki that is copied to electowiki.  It also adds
all of the categories from enwiki to electowiki.

Future work

Future work may involve creating category mappings between electowiki and enwiki, so that it's possible to be smart about category names on electowiki.  It is often not appropriate to copy all of the category names from Wikipedia, and it could be that electowiki category names or categorization schemes differ from enwiki names and schemes.

FAQ

Questions and answers for implementors and users of `ledecopy.py`:

1. What should the CLI look like?
   - `ledecopy.py --fromwiki=enwiki --towiki=electowiki "New York"`

It's okay not to rename the article in the first version, and also it's also okay not to implement --fromwiki and --towiki yet.  We can hardcode fromwiki=enwiki and towiki=electowiki, though please make it easy for a developer to eventually add the option to set the wiki on the CLI and/or configuration eventually.

2. How should the target electowiki article name be chosen?
   Should the enwiki title always determine the electowiki title,
   article key, and local filename, or should `--title`, `--key`, or
   `--local` overrides exist?
In v0.01, the enwiki title will determine the electowiki name.

3. How exactly should "only the lede" be detected?
   Is the lede everything before the first `== Heading ==`, or should
   redirects, comments, hatnotes, infoboxes, short descriptions,
   maintenance templates, and other pre-heading material be handled
   specially?
The lede is all of the visible wikitext prior to the first section.  infoboxes and hatnotes and maintenance templates should be stripped.  It doesn't have to be perfect, it just has to be close enough.

4. Which templates should be copied, stripped, or transformed?
   In particular, what should happen to infoboxes, navboxes,
   maintenance templates, citation templates, short descriptions, and
   Wikipedia-specific templates?
They should generally be stripped, but please be careful not to strip out templates that are important to the lede text.

5. How should references be handled?
   If the copied lede contains `<ref>` tags, should `ledecopy.py` add a
   references section or `{{reflist}}`? Should named refs whose
   definitions are outside the lede be detected?

Yes, ledecopy.py should add a "==References==" and "<refereneces/>" or "{{reflist}}".

6. Which categories should be copied?
   Should hidden categories, maintenance categories, tracking
   categories, stub categories, and Wikipedia administrative categories
   be excluded? Should categories be copied verbatim for now?

I'm not sure.  Just copy all of the visible categories for v0.01.

7. What exact attribution wikitext should be generated?
  - top `{{Wikipedia}}` : just put the title in.
  - bottom `{{Fromwikipedia}}`  : put the title and revid in.

8. How should `ledecopy.py` integrate with `mwsync.py` state?
   Should it only create a local `.mw` file, or should it also
   create/update `mwsync.yaml` so the article is immediately ready for `mwsync.py push`?

it should edit mwsync.yaml so the article is immediately ready for push

9. What should happen if the local `.mw` file already exists?
   Should the tool refuse to overwrite, require `--force`, write a
   backup, or merge somehow?

It should require --force if it exists already.  For that matter, even if the article exists on electowiki, but not locally, that should require --force.

10. Should the tool check whether the target electowiki article already exists before writing local output?  If it exists, should that be an error, a warning, or should the tool still prepare a local overwrite candidate?

It should result in an error that instructs the user to add the article from electowiki first.

11. Which API endpoints should be used?
    The action API works well for this, I think.

12. What should count as a successful run?
    Running and reporting what was changed, and how it was successful.