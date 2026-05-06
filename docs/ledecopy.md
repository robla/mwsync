ledecopy.py is a tool that copies ONLY THE LEDE from an enwiki
article, and then creates an mwsync-compatible local copy of an
article ready to push to electowiki.  It does not require
authentication on either enwiki nor on electowiki.  It puts a pointer
to enwiki at the top of the new article (using
https://electowiki.org/wiki/Template:Wikipedia) and at the bottom of
the article (using
https://electowiki.org/wiki/Template:Fromwikipedia), and notes the
exact revid from enwiki that is copied to electowiki.  It also adds
all of the categories from enwiki to electowiki.

Future work

Future work may involve creating category mappings between electowiki
and enwiki, so that it's possible to be smart about category names on
electowiki.  It is often not appropriate to copy all of the category
names from Wikipedia, and it could be that electowiki category names
or categorization schemes differ from enwiki names and schemes.