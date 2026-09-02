"""Tests for article extraction and the document it produces.

The document these tests assert on is the artifact readers actually see:
``newsletters-web`` renders ``../output/raw/{id}.html`` in an iframe that
supplies no CSS of its own. So "does it carry its own stylesheet" is a
correctness property here, not a cosmetic one.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from lxml import html as lxml_html

from ingestor_tui.backfill.extractor import ExtractionError, extract_article
from ingestor_tui.backfill.mappings import ArticleConfig
from ingestor_tui.backfill.models import ArticleRef

PAGE = """
<html><head>
  <meta property="og:title" content="Meta Title">
  <meta property="article:published_time" content="2026-04-05T09:00:00Z">
</head><body>
  <nav>site chrome</nav>
  <div class="content">
    <p>Real body.</p>
    <img src="/img/pic.png"><a href="/other">link</a>
    <script>tracker();</script><style>.a{}</style>
  </div>
</body></html>
"""

# Mirrors the shape a Substack article actually has: interactive chrome nested
# inside the content container, with meaningful text as its tail.
CHROME_PAGE = """
<html><head>
  <meta property="article:published_time" content="2026-04-05T09:00:00Z">
</head><body>
  <div class="content">
    <h3>I.<div class="anchor-wrap"><button type="button">
      <svg><path d="M0 0"></path></svg>
    </button></div></h3>
    <p>Body text <button type="button"><svg></svg></button>continues here.</p>
    <div class="wrap"><div class="inner"><button>x</button></div></div>
    <figure><img src="/img/a.png"><figcaption>A caption</figcaption></figure>
    <form><input type="email"><button>Subscribe</button></form>
  </div>
</body></html>
"""

PAYWALLED_PAGE = """
<html><head>
  <meta property="article:published_time" content="2026-04-05T09:00:00Z">
</head><body>
  <div class="content"><p>Opening lines...</p></div>
  <div class="paywall"><p>Subscribe to keep reading</p></div>
</body></html>
"""


class StubFetcher:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self, url: str) -> str:
        return self._text


def _extract(
    page: str = PAGE,
    title: str = "T",
    date: datetime | None = None,
    selector: str = "div.content",
    **config_kwargs,
):
    """Extract from a page with the given listing metadata."""
    entry = ArticleRef(
        url="https://x.test/p/a",
        title=title,
        published_at=date if date is not None else datetime(2026, 1, 1, tzinfo=UTC),
    )
    config = ArticleConfig(content_selector=selector, **config_kwargs)
    return extract_article(entry, config, StubFetcher(page))


def _tree(page: str = PAGE, **kwargs) -> lxml_html.HtmlElement:
    return lxml_html.fromstring(_extract(page, **kwargs).content_html)


# --- metadata ---


def test_extract_uses_listing_metadata_first() -> None:
    result = _extract(title="Listing Title")
    assert result.title == "Listing Title"
    assert result.published_at.year == 2026


def test_extract_falls_back_to_page_metadata() -> None:
    entry = ArticleRef(url="https://x.test/p/a", title="", published_at=None)
    result = extract_article(
        entry, ArticleConfig(content_selector="div.content"), StubFetcher(PAGE)
    )
    assert result.title == "Meta Title"
    assert result.published_at.strftime("%Y-%m-%d") == "2026-04-05"


def test_extract_without_a_date_anywhere_raises() -> None:
    entry = ArticleRef(url="https://x.test/p/a", title="T", published_at=None)
    page = "<html><body><p>x</p></body></html>"
    with pytest.raises(ExtractionError, match="publish date"):
        extract_article(entry, ArticleConfig(), StubFetcher(page))


# --- content selection ---


def test_extract_narrows_to_content_and_strips_scripts() -> None:
    html = _extract().content_html
    assert "Real body." in html
    assert "site chrome" not in html
    assert "tracker()" not in html


def test_extract_absolutizes_links_and_images() -> None:
    html = _extract().content_html
    assert "https://x.test/img/pic.png" in html
    assert "https://x.test/other" in html


def test_extract_falls_back_when_selector_misses() -> None:
    """A stale selector still yields content rather than failing the run."""
    html = _extract(selector="div.nonexistent").content_html
    assert "Real body." in html


# --- the stored document ---


def test_extract_wraps_in_a_standalone_document() -> None:
    html = _extract().content_html
    assert html.startswith("<!DOCTYPE html>")
    assert 'charset="utf-8"' in html
    assert 'rel="canonical" href="https://x.test/p/a"' in html
    assert 'name="viewport"' in html


def test_document_carries_its_own_stylesheet() -> None:
    """The viewer iframe supplies no CSS, so the document has to."""
    tree = _tree()
    styles = tree.cssselect("style")
    assert len(styles) == 1
    css = styles[0].text_content()
    # Without this an image renders at its width attribute and blows the
    # column open — the specific symptom that made backfilled articles look
    # unlike the emails beside them.
    assert "max-width: 100%" in css
    assert tree.cssselect("div.email-body"), "body wrapper missing"


def test_page_style_blocks_do_not_survive_into_the_document() -> None:
    """The page's own <style> refers to stylesheets we never captured."""
    assert ".a{}" not in _extract().content_html


def test_document_sets_the_title_but_injects_no_heading() -> None:
    """app.js builds list previews from <body> text, so a heading would
    prepend duplicate subject text to every preview row."""
    tree = _tree(title="Listing Title")
    assert tree.cssselect("title")[0].text_content() == "Listing Title"
    assert not tree.cssselect("body h1")


def test_titles_are_escaped_into_the_title_element() -> None:
    tree = _tree(title='Quotes "and" <angles>')
    assert tree.cssselect("title")[0].text_content() == 'Quotes "and" <angles>'


# --- chrome stripping ---


def test_interactive_chrome_is_removed() -> None:
    tree = _tree(CHROME_PAGE)
    assert not tree.cssselect("button")
    assert not tree.cssselect("form")
    assert not tree.cssselect("input")
    # Every icon observed in the corpus is a button child, so it goes too.
    assert not tree.cssselect("svg")


def test_stripping_preserves_the_text_that_followed() -> None:
    """lxml keeps an element's trailing text on the element itself, so a naive
    remove() eats half a sentence around an inline button."""
    text = " ".join(_tree(CHROME_PAGE).text_content().split())
    assert "Body text continues here." in text


def test_emptied_wrappers_are_dropped_but_media_survives() -> None:
    tree = _tree(CHROME_PAGE)
    assert not tree.cssselect("div.anchor-wrap")
    assert not tree.cssselect("div.wrap")
    assert tree.cssselect("figure"), "figure holds an image and must survive"
    assert tree.cssselect("img")
    assert "A caption" in tree.text_content()


def test_headings_survive_having_their_anchor_stripped() -> None:
    assert _tree(CHROME_PAGE).cssselect("h3")[0].text_content().strip() == "I."


def test_strip_selectors_can_be_overridden_per_mapping() -> None:
    """A publication whose real content uses <button> opts out via the mapping."""
    tree = _tree(CHROME_PAGE, strip_selectors=("script", "style"))
    assert tree.cssselect("button")


# --- paywalled previews ---


def test_paywalled_pages_are_still_written_with_a_source_note() -> None:
    """Title and opening lines have completeness value; the note stops two
    sentences from being presented as the whole piece."""
    tree = _tree(PAYWALLED_PAGE)
    assert "Opening lines..." in tree.text_content()
    note = tree.cssselect("p.backfill-note")
    assert note, "expected a subscriber-only note"
    assert "https://x.test/p/a" in note[0].text_content()


def test_full_articles_get_no_note() -> None:
    assert not _tree().cssselect("p.backfill-note")


def test_the_note_is_a_footer_so_previews_are_unaffected() -> None:
    """app.js truncates previews from the start, so a trailing note is safe
    where a leading one would not be."""
    body = _tree(PAYWALLED_PAGE).cssselect("body")[0]
    text = " ".join(body.text_content().split())
    assert text.startswith("Opening lines...")
