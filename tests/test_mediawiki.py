# Offline unit tests for the MediaWiki layer: URL parsing, redirect detection
# in both --no-follow (default) and --follow modes, and siteinfo trimming. The
# network is stubbed by replacing urlopen with canned JSON, so these stay in the
# fast offline suite; the live-network clone test belongs in the integration group.

import json

import pytest

from mwmap.core import mediawiki


def test_parse_wiki_path_url():
    title, api_url, location = mediawiki.parse_mediawiki_page_url(
        "https://electowiki.org/wiki/California"
    )
    assert title == "California"
    assert api_url == "https://electowiki.org/w/api.php"
    assert location == "https://electowiki.org/w/"


def test_parse_index_php_url():
    title, api_url, _location = mediawiki.parse_mediawiki_page_url(
        "https://example.org/w/index.php?title=Foo_Bar"
    )
    assert title == "Foo Bar"
    assert api_url == "https://example.org/w/api.php"


def test_parse_rejects_non_http():
    with pytest.raises(SystemExit):
        mediawiki.parse_mediawiki_page_url("ftp://example.org/wiki/Foo")


class _FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_urlopen(monkeypatch, by_query):
    """Route requests to a canned payload by a substring of the request URL."""

    def fake_urlopen(req, timeout=30):
        url = req.full_url
        for needle, payload in by_query:
            if needle in url:
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected request URL: {url}")

    monkeypatch.setattr(mediawiki.request, "urlopen", fake_urlopen)


def test_fetch_detects_redirect_without_following(monkeypatch):
    payload = {
        "query": {
            "pages": [
                {
                    "pageid": 42,
                    "title": "California",
                    "redirect": True,
                    "revisions": [
                        {
                            "revid": 99,
                            "parentid": 0,
                            "timestamp": "2026-01-01T00:00:00Z",
                            "slots": {
                                "main": {
                                    "contentmodel": "wikitext",
                                    "content": "#REDIRECT [[State of California]]",
                                }
                            },
                        }
                    ],
                }
            ]
        }
    }
    _stub_urlopen(monkeypatch, [("", payload)])
    content, metadata, redirect = mediawiki.fetch_mediawiki_page(
        "https://x/w/api.php", "California"
    )
    assert content.startswith("#REDIRECT")
    assert metadata["pageid"] == 42
    assert metadata["redirect"] is True
    assert redirect == {"followed": False, "from": "California", "to": "State of California"}


def test_fetch_follows_redirect_when_requested(monkeypatch):
    follow_payload = {
        "query": {
            "redirects": [{"from": "California", "to": "State of California"}],
            "pages": [
                {
                    "pageid": 500,
                    "title": "State of California",
                    "revisions": [
                        {
                            "revid": 200,
                            "parentid": 199,
                            "timestamp": "2026-02-02T00:00:00Z",
                            "slots": {
                                "main": {"contentmodel": "wikitext", "content": "Real article"}
                            },
                        }
                    ],
                }
            ],
        }
    }
    # Only a request carrying redirects=1 should be served, proving --follow sends it.
    _stub_urlopen(monkeypatch, [("redirects=1", follow_payload)])
    content, metadata, redirect = mediawiki.fetch_mediawiki_page(
        "https://x/w/api.php", "California", follow_redirects=True
    )
    assert content == "Real article"
    assert metadata["pageid"] == 500
    assert redirect == {"followed": True, "from": "California", "to": "State of California"}


def test_fetch_non_redirect_has_no_note(monkeypatch):
    payload = {
        "query": {
            "pages": [
                {
                    "pageid": 7,
                    "title": "Maine",
                    "revisions": [
                        {
                            "revid": 5,
                            "parentid": 4,
                            "timestamp": "2026-03-03T00:00:00Z",
                            "slots": {
                                "main": {"contentmodel": "wikitext", "content": "Maine article"}
                            },
                        }
                    ],
                }
            ]
        }
    }
    _stub_urlopen(monkeypatch, [("", payload)])
    _content, metadata, redirect = mediawiki.fetch_mediawiki_page("https://x/w/api.php", "Maine")
    assert redirect is None
    assert metadata["redirect"] is False


def test_fetch_siteinfo_trims_payload(monkeypatch):
    payload = {
        "query": {
            "general": {
                "sitename": "Electowiki",
                "server": "https://electowiki.org",
                "articlepath": "/wiki/$1",
                "scriptpath": "/w",
                "base": "https://electowiki.org/wiki/Main_Page",
                "lang": "en",
                "generator": "MediaWiki 1.40",
                "ignored_extra_field": "dropped",
            },
            "namespaces": {
                "0": {"id": 0, "name": "", "canonical": ""},
                "14": {"id": 14, "name": "Category", "canonical": "Category"},
            },
        }
    }
    _stub_urlopen(monkeypatch, [("siteinfo", payload)])
    info = mediawiki.fetch_siteinfo("https://x/w/api.php")
    assert info["general"]["articlepath"] == "/wiki/$1"
    assert "ignored_extra_field" not in info["general"]
    assert info["namespaces"]["14"] == {"name": "Category", "canonical": "Category"}
