# Makefile to build HTML files from Markdown files in this directory.

MD_FILES := $(wildcard *.md)
HTML_FILES := $(patsubst %.md,html/%.html,$(MD_FILES))

.PHONY: all clean

all: $(HTML_FILES)

html/%.html: %.md
	@mkdir -p html
	pandoc -s -f markdown -t html -o $@ $<

clean:
	rm -rf html
