"""Tests for archive listing readers, especially pagination termination."""

from __future__ import annotations

import json

import pytest

from ingestor_tui.backfill.listing import ListingError, read_listing
from ingestor_tui.backfill.mappings import BackfillMapping


class FakeFetcher:
    """Stands in for Fetcher, serving canned responses and recording calls."""

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses
        self.requested: list[str] = []

    def get_text(self, url: str) -> str:
        self.requested.append(url)
        if url not in self._responses:
            raise AssertionError(f"unexpected request: {url}")
        return self._responses[url]

    def get_json(self, url: str) -> object:
        return json.loads(self.get_text(url))


def _json_mapping(**listing_overrides) -> BackfillMapping:
    listing = {
        "mode": "json",
        "url_template": "https://x.test/api?offset={offset}&limit={limit}",
        "pagination": {"type": "offset", "page_size": 2, "max_pages": 5},
        "items_path": "",
        "fields": {"url": "canonical_url", "title": "title", "date": "post_date"},
    }
    listing.update(listing_overrides)
    return BackfillMapping.from_dict(
        "Test", {"archive_url": "https://x.test/archive", "listing": listing}
    )


def _html_mapping(**listing_overrides) -> BackfillMapping:
    listing = {
        "mode": "html",
        "url_template": "https://x.test/archive?page={page}",
        "pagination": {"type": "page", "start": 1, "max_pages": 5},
        "item_selector": "article.post",
        "fields": {
            "url": {"selector": "a", "attr": "href"},
            "title": {"selector": "a", "attr": "text"},
            "date": {"selector": "time", "attr": "datetime"},
        },
    }
    listing.update(listing_overrides)
    return BackfillMapping.from_dict(
        "Test", {"archive_url": "https://x.test/archive", "listing": listing}
    )


def _posts(*slugs: str) -> str:
    return json.dumps(
        [
            {
                "canonical_url": f"https://x.test/p/{slug}",
                "title": slug.replace("-", " ").title(),
                "post_date": "2026-05-01T10:00:00.000Z",
            }
            for slug in slugs
        ]
    )


# --- json mode ---


def test_json_paginates_until_empty() -> None:
    fetcher = FakeFetcher(
        {
            "https://x.test/api?offset=0&limit=2": _posts("one", "two"),
            "https://x.test/api?offset=2&limit=2": _posts("three"),
            "https://x.test/api?offset=4&limit=2": "[]",
        }
    )
    refs = read_listing(_json_mapping(), fetcher)
    assert [r.url.rsplit("/", 1)[-1] for r in refs] == ["one", "two", "three"]


def test_json_parses_dates_and_titles() -> None:
    fetcher = FakeFetcher(
        {"https://x.test/api?offset=0&limit=2": _posts("hello-world"),
         "https://x.test/api?offset=2&limit=2": "[]"}
    )
    ref = read_listing(_json_mapping(), fetcher)[0]
    assert ref.title == "Hello World"
    assert ref.published_at is not None
    assert ref.published_at.strftime("%Y-%m-%d") == "2026-05-01"


def test_json_short_page_does_not_stop_pagination() -> None:
    """A page smaller than page_size is not the end of the archive.

    Substack's endpoint returns short pages mid-archive; treating one as the
    end truncated a 113-article archive to 23 during development.
    """
    fetcher = FakeFetcher(
        {
            "https://x.test/api?offset=0&limit=2": _posts("one"),
            "https://x.test/api?offset=2&limit=2": _posts("two", "three"),
            "https://x.test/api?offset=4&limit=2": "[]",
        }
    )
    assert len(read_listing(_json_mapping(), fetcher)) == 3


def test_json_items_path_resolves_nested_array() -> None:
    payload = json.dumps({"data": {"posts": json.loads(_posts("nested"))}})
    fetcher = FakeFetcher(
        {"https://x.test/api?offset=0&limit=2": payload,
         "https://x.test/api?offset=2&limit=2": json.dumps({"data": {"posts": []}})}
    )
    refs = read_listing(_json_mapping(items_path="data.posts"), fetcher)
    assert len(refs) == 1


def test_json_items_path_pointing_at_non_list_errors() -> None:
    fetcher = FakeFetcher({"https://x.test/api?offset=0&limit=2": json.dumps({"data": 5})})
    with pytest.raises(ListingError, match="expected a list"):
        read_listing(_json_mapping(items_path="data"), fetcher)


# --- the pagination guard ---


def test_repeated_page_stops_pagination() -> None:
    """The Substack case: ?offset= is accepted but page 2 repeats page 1.

    Without the "no new URLs" guard this would spin to max_pages, and the
    caller would have no way to tell a truncated read from a complete one.
    """
    page = _posts("one", "two")
    fetcher = FakeFetcher({f"https://x.test/api?offset={n}&limit=2": page for n in (0, 2, 4, 6, 8)})
    refs = read_listing(_json_mapping(), fetcher)

    assert len(refs) == 2
    # Stopped after detecting the repeat, rather than walking all 5 pages.
    assert len(fetcher.requested) == 2


def test_duplicate_urls_within_a_page_are_deduped() -> None:
    fetcher = FakeFetcher(
        {"https://x.test/api?offset=0&limit=2": _posts("same", "same"),
         "https://x.test/api?offset=2&limit=2": "[]"}
    )
    assert len(read_listing(_json_mapping(), fetcher)) == 1


def test_limit_stops_early() -> None:
    fetcher = FakeFetcher({"https://x.test/api?offset=0&limit=2": _posts("a", "b")})
    refs = read_listing(_json_mapping(), fetcher, limit=1)
    assert len(refs) == 1
    assert len(fetcher.requested) == 1


def test_max_pages_caps_the_walk() -> None:
    """Even a genuinely endless archive stops at max_pages."""
    pages = {
        f"https://x.test/api?offset={n * 2}&limit=2": _posts(f"p{n}a", f"p{n}b")
        for n in range(10)
    }
    mapping = _json_mapping(pagination={"type": "offset", "page_size": 2, "max_pages": 3})
    assert len(read_listing(mapping, FakeFetcher(pages))) == 6


# --- html mode ---


HTML_PAGE = """
<html><body>
  <article class="post">
    <a href="/p/first">First Post</a><time datetime="2026-03-01">Mar 1</time>
  </article>
  <article class="post">
    <a href="/p/second">Second Post</a><time datetime="2026-03-02">Mar 2</time>
  </article>
</body></html>
"""


def test_html_extracts_and_absolutizes() -> None:
    fetcher = FakeFetcher(
        {"https://x.test/archive?page=1": HTML_PAGE,
         "https://x.test/archive?page=2": "<html><body></body></html>"}
    )
    refs = read_listing(_html_mapping(), fetcher)
    assert [r.url for r in refs] == ["https://x.test/p/first", "https://x.test/p/second"]
    assert refs[0].title == "First Post"
    assert refs[0].published_at.strftime("%Y-%m-%d") == "2026-03-01"


def test_html_stops_when_selector_matches_nothing() -> None:
    empty = "<html><body><p>no posts</p></body></html>"
    fetcher = FakeFetcher({"https://x.test/archive?page=1": empty})
    assert read_listing(_html_mapping(), fetcher) == []


def test_html_follows_next_page_links() -> None:
    page1 = HTML_PAGE.replace("</body>", '<a class="next" href="/archive/2">Older</a></body>')
    page2 = (
        '<html><body><article class="post">'
        '<a href="/p/third">Third</a></article></body></html>'
    )
    fetcher = FakeFetcher({"https://x.test/archive": page1, "https://x.test/archive/2": page2})

    mapping = _html_mapping(
        url_template="https://x.test/archive",
        pagination={"type": "none", "max_pages": 5},
        next_page_selector="a.next",
    )
    refs = read_listing(mapping, fetcher)
    assert [r.url.rsplit("/", 1)[-1] for r in refs] == ["first", "second", "third"]


def test_html_next_page_loop_is_bounded() -> None:
    """A next-link pointing back at itself must not loop forever."""
    page = HTML_PAGE.replace("</body>", '<a class="next" href="/archive">Older</a></body>')
    fetcher = FakeFetcher({"https://x.test/archive": page})
    mapping = _html_mapping(
        url_template="https://x.test/archive",
        pagination={"type": "none", "max_pages": 5},
        next_page_selector="a.next",
    )
    assert len(read_listing(mapping, fetcher)) == 2
    assert len(fetcher.requested) == 1


def test_rendered_mode_without_playwright_is_actionable(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fail_playwright(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_playwright)

    mapping = _html_mapping(mode="rendered", pagination={"type": "none"},
                            url_template="https://x.test/archive")
    with pytest.raises(ListingError, match="uv sync --extra rendered"):
        read_listing(mapping, FakeFetcher({}))
