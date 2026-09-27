"""Tests for Substack embeds surviving extraction *and* markdown conversion.

Substack renders cross-links — an embedded post, a "read full story" digest
card, a recommended publication — as card components. Trafilatura's
boilerplate filter discards any element whose class contains ``embed`` (and
several other words these cards use), so the markdown kept the author's
lead-in ("…here is good:") and silently lost what it pointed at.

These tests therefore assert on the **markdown**, produced by the real
``BackfillWriter`` → gmail-ingestor ``MarkdownConverter`` → trafilatura path.
Asserting on the extracted HTML alone would pass while the bug stood: the card
was always present in the HTML.

Fixtures are trimmed copies of real markup from
nabeelqu.substack.com/p/reflections-on-palantir (embedded post in a footnote),
newsletter.theindianotes.com (digest cards, publication card, Instagram) and a
Substack YouTube embed.
"""

from __future__ import annotations

import html
import json
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lxml import html as lxml_html

from ingestor_tui.backfill.extractor import extract_article
from ingestor_tui.backfill.mappings import ArticleConfig, BackfillMapping
from ingestor_tui.backfill.models import ArticleRef
from ingestor_tui.backfill.writer import BackfillWriter

ARTICLE_URL = "https://nabeelqu.substack.com/p/reflections-on-palantir"


def _attrs(data: dict) -> str:
    """Encode a dict the way Substack puts it in ``data-attrs``."""
    return html.escape(json.dumps(data, ensure_ascii=False), quote=True)


EMBEDDED_POST_ATTRS = {
    "id": 149158514,
    "url": "https://tedmabrey.substack.com/p/sorry-that-isnt-an-fde",
    "publication_id": 2225975,
    "publication_name": "Ted’s Substack",
    "title": "Sorry, that isn't an FDE",
    "truncated_body_text": "One of Palantir’s secrets in the Zero to One sense of the word "
    "has been the FDE.",
    "date": "2024-09-20T15:33:44.707Z",
    "bylines": [{"id": 144754224, "name": "Ted Mabrey", "handle": "tedmabrey"}],
    "type": "newsletter",
}


def _embedded_post(attrs: str = _attrs(EMBEDDED_POST_ATTRS)) -> str:
    """One EmbeddedPostToDOM card, as Substack serves it."""
    return f"""<div data-attrs="{attrs}" data-component-name="EmbeddedPostToDOM"
      class="embedded-post-wrap"><a native="true"
      href="https://tedmabrey.substack.com/p/sorry-that-isnt-an-fde?utm_source=substack&amp;utm_campaign=post_embed&amp;utm_medium=web"
      class="embedded-post"><div class="embedded-post-header"><img
      src="https://substackcdn.com/image/fetch/logo.png"
      class="embedded-post-publication-logo"><span
      class="embedded-post-publication-name">Ted’s Substack</span></div><div
      class="embedded-post-title-wrapper"><div class="embedded-post-title">Sorry, that isn't an
      FDE</div></div><div class="embedded-post-body">One of Palantir’s secrets in the Zero to One
      sense of the word has been the FDE.</div><div class="embedded-post-cta-wrapper"><span
      class="embedded-post-cta">Read more</span></div><div class="embedded-post-meta">2 years ago ·
      40 likes · 3 comments · Ted Mabrey</div></a></div>"""


def _page(body: str) -> str:
    """Wrap body markup in a Substack-shaped article page."""
    return f"""<html><head>
      <meta property="article:published_time" content="2024-10-15T19:19:09Z">
    </head><body>
      <div class="available-content"><div dir="auto" class="body markup">
        <p>Palantir is hot now. The company recently joined the S&amp;P 500, and the
        stock is on a tear. For long-time employees and alumni this feels deeply weird.</p>
        {body}
        <p>I left last year, but never wrote publicly about what I learned there. There
        is a lot about the company that people do not understand.</p>
      </div></div>
    </body></html>"""


# The exact shape of the Palantir failure: the card sits inside a footnote,
# directly after its lead-in paragraph.
FOOTNOTE_PAGE = _page(
    f"""<div data-component-name="FootnoteToDOM" class="footnote"><a id="footnote-2"
      href="{ARTICLE_URL}#footnote-anchor-2" class="footnote-number">2</a><div
      class="footnote-content"><p>Ted Mabrey’s essay on the FDE model here is good: </p>
      {_embedded_post()}</div></div>"""
)

INLINE_PAGE = _page(
    f"<p>Ted Mabrey’s essay on the FDE model here is good:</p>{_embedded_post()}"
    "<p>That is the forward deployed engineer model in a nutshell.</p>"
)


def _digest_card(url: str, title: str, authors: str, date: str) -> str:
    """One DigestPostEmbed card. No data-attrs; hashed class names only."""
    profile = "https://substack.com/profile/3092099-dharmesh-ba"
    return f"""<div data-component-name="DigestPostEmbed" class="digestPostEmbed-flwiST"><a
      href="{url}" rel="noopener" target="_blank"><div class="pencraft pc-display-flex
      pc-gap-16 pc-reset"><div style="width:70px;height:70px;"
      class="pencraft pc-reset"><picture><img
      src="https://substackcdn.com/image/fetch/thumb.png" alt="{title}" width="140"
      height="140" class="img-OACg1c"></picture></div><div class="pencraft pc-display-flex
      pc-flexDirection-column pc-reset"><h4
      class="pencraft pc-reset weight-bold-DmI9lw">{title}</h4><div
      class="pencraft pc-display-flex pc-gap-4 pc-reset"><div class="pencraft pc-reset
      meta-EgzBVA"><a href="{profile}" class="inheritColor-WetTGJ">{authors}</a></div><div
      class="pencraft pc-reset">·</div><div
      class="pencraft pc-reset meta-EgzBVA">{date}</div></div><div
      class="pencraft pc-display-flex pc-reset"><a href="{url}" class="pencraft pc-reset"><div
      class="pencraft pc-display-flex link-HREYZo"><span class="pencraft pc-reset">Read full
      story</span></div></a></div></div></div></a></div>"""


DIGEST_PAGE = _page(
    "<p>If you are new to The India Notes, here are a few of my super hit editions</p>"
    + _digest_card(
        "https://newsletter.theindianotes.com/p/building-trust-in-digital-payments",
        "Building trust in digital payments",
        "Dharmesh Ba",
        "October 31, 2023",
    )
    + _digest_card(
        "https://newsletter.theindianotes.com/p/astrotalk-ux-audit",
        "Astrotalk - UX Audit",
        "Dharmesh Ba and Sidhant Bhutani",
        "October 24, 2023",
    )
)

PUBLICATION_ATTRS = {
    "id": 5541954,
    "name": "TechnicAly’s Substack",
    "base_url": "https://technicalyspeaking.substack.com",
    "hero_text": "My personal Substack",
    "author_name": "TechnicAly Speaking",
    "show_subscribe": True,
}

PUBLICATION_PAGE = _page(
    "<p>Alysha Lobo runs a fantastic newsletter on robotics which I highly recommend.</p>"
    f"""<div data-attrs="{_attrs(PUBLICATION_ATTRS)}"
      data-component-name="EmbeddedPublicationToDOMWithSubscribe"
      class="embedded-publication-wrap"><div class="embedded-publication show-subscribe"><a
      native="true" href="https://technicalyspeaking.substack.com?utm_source=substack&amp;utm_campaign=publication_embed"
      class="embedded-publication-link-part"><img src="https://substackcdn.com/logo.png"
      class="embedded-publication-logo"><span class="embedded-publication-name">TechnicAly’s
      Substack</span><div class="embedded-publication-hero-text">My personal Substack</div><div
      class="embedded-publication-author-name">By TechnicAly Speaking</div></a></div></div>"""
)


def _youtube(video_id: str, start: str | None = None) -> str:
    attrs = _attrs({"videoId": video_id, "startTime": start, "endTime": None})
    return f"""<div id="youtube2-{video_id}" data-attrs="{attrs}"
      data-component-name="Youtube2ToDOM" class="youtube-wrap"><div class="youtube-inner"><iframe
      src="https://www.youtube-nocookie.com/embed/{video_id}?rel=0&amp;autoplay=0"
      frameborder="0" loading="lazy" width="728" height="409"></iframe></div></div>"""


YOUTUBE_PAGE = _page(
    "<p>Watch the talk first:</p>" + _youtube("IuIpgArEZig") + _youtube("3Fx5Q8xGU8k", "12s")
)

# Substack inlines the whole Instagram embed document as a data: URL; the only
# place the post's address appears is the blockquote's data-instgrm-permalink.
_INSTAGRAM_DOC = (
    '<!doctype html><html><body><blockquote class="instagram-media" data-instgrm-captioned '
    'data-instgrm-permalink="https://instagram.com/p/DG4hSCxsxfL/?utm_source=ig_embed&amp;'
    'utm_campaign=loading" data-instgrm-version="14"></blockquote>'
    '<script async src="https://www.instagram.com/embed.js"></script></body></html>'
)
INSTAGRAM_PAGE = _page(
    "<p>The ministry posted this on Instagram:</p>"
    f"""<iframe src="data:text/html;charset=utf-8,{urllib.parse.quote(_INSTAGRAM_DOC)}"
      title="Instagram post" frameborder="0" scrolling="no"
      class="instagram-embed-frame"></iframe>"""
)


class StubFetcher:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self, url: str) -> str:
        return self._text


@pytest.fixture
def mapping() -> BackfillMapping:
    return BackfillMapping.from_dict(
        "Nabeel Qureshi",
        {
            "label_id": "Label_123",
            "archive_url": "https://nabeelqu.substack.com/archive",
            "sender": "Nabeel Qureshi <nabeelqu@substack.com>",
            "listing": {
                "mode": "json",
                "url_template": "https://x.test/api?offset={offset}&limit={limit}",
                "pagination": {"type": "offset"},
                "fields": {"url": "url", "title": "title"},
            },
            "article": {"content_selector": "div.available-content"},
        },
    )


def _html(page: str) -> str:
    """The stored, self-contained HTML document for a page."""
    ref = ArticleRef(
        url=ARTICLE_URL,
        title="Reflections on Palantir",
        published_at=datetime(2024, 10, 15, 19, 19, 9, tzinfo=UTC),
    )
    config = ArticleConfig(content_selector="div.available-content")
    return extract_article(ref, config, StubFetcher(page)).content_html


def _markdown(page: str, mapping: BackfillMapping, tmp_path: Path) -> str:
    """Run a page through extraction *and* the real markdown writer."""
    ref = ArticleRef(
        url=ARTICLE_URL,
        title="Reflections on Palantir",
        published_at=datetime(2024, 10, 15, 19, 19, 9, tzinfo=UTC),
    )
    config = ArticleConfig(content_selector="div.available-content")
    article = extract_article(ref, config, StubFetcher(page))
    writer = BackfillWriter(tmp_path / "markdown", tmp_path / "raw")
    written = writer.write("web-0123456789abcdef", article, mapping)
    return written.markdown_path.read_text(encoding="utf-8")


# --- embedded posts ---


def test_embedded_post_in_a_footnote_survives_as_a_link(mapping, tmp_path) -> None:
    """The reported bug: the lead-in survived, the essay it introduced did not."""
    md = _markdown(FOOTNOTE_PAGE, mapping, tmp_path)
    assert "Ted Mabrey’s essay on the FDE model here is good:" in md
    assert (
        "[Sorry, that isn't an FDE](https://tedmabrey.substack.com/p/sorry-that-isnt-an-fde)" in md
    )


def test_embedded_post_in_the_body_survives_as_a_link(mapping, tmp_path) -> None:
    md = _markdown(INLINE_PAGE, mapping, tmp_path)
    assert (
        "[Sorry, that isn't an FDE](https://tedmabrey.substack.com/p/sorry-that-isnt-an-fde)" in md
    )
    assert "That is the forward deployed engineer model in a nutshell." in md


def test_embedded_post_keeps_its_byline_and_excerpt(mapping, tmp_path) -> None:
    """The card showed who wrote it and what it says; so should the markdown."""
    md = _markdown(INLINE_PAGE, mapping, tmp_path)
    assert "Ted Mabrey" in md
    assert "Ted’s Substack" in md
    assert "One of Palantir’s secrets in the Zero to One sense" in md


def test_embedded_post_is_labelled(mapping, tmp_path) -> None:
    """The label tells a reader a card stood here — and keeps the line alive."""
    md = _markdown(INLINE_PAGE, mapping, tmp_path)
    assert "Embedded post: [Sorry, that isn't an FDE](" in md


def test_short_uncredited_post_still_survives(mapping, tmp_path) -> None:
    """trafilatura deletes a short <p> that is over 80% link text. Without the
    label, a two-word title with no byline and no excerpt is exactly that."""
    attrs = _attrs({"url": "https://x.substack.com/p/on-taste", "title": "On taste"})
    md = _markdown(_page(f"<p>See:</p>{_embedded_post(attrs)}"), mapping, tmp_path)
    assert "[On taste](https://x.substack.com/p/on-taste)" in md


def test_excerpt_quote_holds_no_link() -> None:
    """trafilatura strips link targets inside quotes, so the link line must
    sit outside the blockquote."""
    tree = lxml_html.fromstring(_html(INLINE_PAGE))
    quotes = tree.cssselect("blockquote")
    assert quotes, "expected the excerpt as a blockquote"
    assert not any(q.cssselect("a") for q in quotes)


def test_replacements_carry_no_class(mapping) -> None:
    """Classes are what trafilatura's boilerplate filter matches on."""
    tree = lxml_html.fromstring(_html(INLINE_PAGE))
    inserted = tree.cssselect("[data-backfill-embed]")
    assert inserted
    assert not any(el.get("class") for el in inserted)


def test_embedded_post_link_is_the_canonical_url(mapping) -> None:
    """data-attrs carries the clean URL; the card's href carries utm tracking."""
    tree = lxml_html.fromstring(_html(INLINE_PAGE))
    hrefs = [a.get("href") for a in tree.iter("a")]
    assert "https://tedmabrey.substack.com/p/sorry-that-isnt-an-fde" in hrefs
    assert not any("utm_campaign=post_embed" in h for h in hrefs)


def test_embedded_post_drops_card_chrome(mapping, tmp_path) -> None:
    """Relative dates and like counts are card furniture, stale the day after."""
    md = _markdown(INLINE_PAGE, mapping, tmp_path)
    assert "40 likes" not in md
    assert "Read more" not in md


def test_malformed_attrs_leave_the_card_in_place() -> None:
    """Unreadable data-attrs must not raise or delete the card — it degrades to
    exactly the pre-fix behaviour rather than losing more."""
    page = _page(f"<p>Lead-in:</p>{_embedded_post(attrs='{not json')}")
    tree = lxml_html.fromstring(_html(page))
    assert tree.cssselect('[data-component-name="EmbeddedPostToDOM"]')


def test_text_following_an_embed_is_preserved() -> None:
    """lxml stores trailing text on the element being replaced."""
    page = _page(f"<div>Before {_embedded_post()} and after.</div>")
    text = " ".join(lxml_html.fromstring(_html(page)).text_content().split())
    assert "and after." in text


# --- digest cards ---


def test_digest_cards_survive_as_links(mapping, tmp_path) -> None:
    md = _markdown(DIGEST_PAGE, mapping, tmp_path)
    assert (
        "[Building trust in digital payments]"
        "(https://newsletter.theindianotes.com/p/building-trust-in-digital-payments)" in md
    )
    assert "[Astrotalk - UX Audit](https://newsletter.theindianotes.com/p/astrotalk-ux-audit)" in md


def test_digest_cards_keep_authors_and_date(mapping, tmp_path) -> None:
    md = _markdown(DIGEST_PAGE, mapping, tmp_path)
    assert "Dharmesh Ba · October 31, 2023" in md
    assert "Dharmesh Ba and Sidhant Bhutani · October 24, 2023" in md
    assert "Read full story" not in md


# --- recommended publications ---


def test_embedded_publication_survives_as_a_link(mapping, tmp_path) -> None:
    md = _markdown(PUBLICATION_PAGE, mapping, tmp_path)
    assert "[TechnicAly’s Substack](https://technicalyspeaking.substack.com)" in md
    assert "My personal Substack" in md
    assert "TechnicAly Speaking" in md


# --- media embeds ---


def test_youtube_embed_gains_a_link(mapping, tmp_path) -> None:
    """An iframe has no text, so the markdown held no trace of the video."""
    md = _markdown(YOUTUBE_PAGE, mapping, tmp_path)
    assert "https://www.youtube.com/watch?v=IuIpgArEZig" in md
    assert "https://www.youtube.com/watch?v=3Fx5Q8xGU8k&t=12s" in md


def test_youtube_embed_keeps_its_iframe() -> None:
    """Additive only: the stored document still embeds the video."""
    tree = lxml_html.fromstring(_html(YOUTUBE_PAGE))
    assert len(tree.cssselect("iframe")) == 2


def test_instagram_embed_gains_a_link(mapping, tmp_path) -> None:
    md = _markdown(INSTAGRAM_PAGE, mapping, tmp_path)
    assert "https://instagram.com/p/DG4hSCxsxfL/" in md
    assert "utm_source=ig_embed" not in md
