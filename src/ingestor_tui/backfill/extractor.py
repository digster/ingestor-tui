"""Article page → title, publish date, and a clean content HTML fragment.

The output of this module is what lands in ``../output/raw/{id}.html``, so it
deliberately stores the *content subtree* rather than the whole page. Two
reasons: ``newsletters-web`` renders that file directly inside an iframe, where
full site chrome (nav, subscribe walls, cookie banners) would be noise; and
trafilatura produces markedly better markdown from a narrowed subtree than from
a full Substack page.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime

from lxml import etree
from lxml import html as lxml_html

from ingestor_tui.backfill.fetcher import Fetcher
from ingestor_tui.backfill.mappings import ArticleConfig
from ingestor_tui.backfill.models import ArticleRef, ExtractedArticle
from ingestor_tui.backfill.parsing import parse_date

logger = logging.getLogger(__name__)

# Stripped from the stored fragment. Scripts and styles cannot help a reader
# and only bloat the archive; <noscript> duplicates content that is already
# present once JS-rendered markup has been captured.
_STRIP_TAGS = ("script", "style", "noscript", "template")

# Fallback content selectors, tried in order when the mapping supplies none or
# its selector matches nothing. Ordered most- to least-specific.
_FALLBACK_CONTENT_SELECTORS = (
    "div.available-content",
    "article",
    "main",
    "div.post-content",
    "div.entry-content",
    "div.content",
)


class ExtractionError(RuntimeError):
    """Raised when an article page yields no usable content."""


def extract_article(
    ref: ArticleRef,
    config: ArticleConfig,
    fetcher: Fetcher,
) -> ExtractedArticle:
    """Fetch one article page and reduce it to the parts we persist.

    Listing metadata wins where present: the archive listing is a more reliable
    source of title and date than page markup, which often carries a decorated
    or truncated variant. Page values only fill the gaps.
    """
    markup = fetcher.get_text(ref.url)

    try:
        tree = lxml_html.fromstring(markup)
    except Exception as e:
        raise ExtractionError(f"Could not parse {ref.url}: {e}") from e

    # Resolve every relative href/src against the article URL before anything
    # is extracted, so images and links still work from ../output/raw/.
    tree.make_links_absolute(ref.url, resolve_base_href=True)

    title = ref.title or _extract_title(tree, config)
    if not title:
        raise ExtractionError(f"No title found for {ref.url}")

    published_at = ref.published_at or _extract_date(tree, config)
    if published_at is None:
        raise ExtractionError(f"No publish date found for {ref.url}")

    content_html = _extract_content(tree, config, ref.url)

    return ExtractedArticle(
        url=ref.url,
        title=title,
        published_at=published_at,
        content_html=content_html,
    )


def _extract_title(tree: lxml_html.HtmlElement, config: ArticleConfig) -> str:
    """Title from the configured selector, then og:title, then <h1>, then <title>."""
    if config.title_selector:
        found = tree.cssselect(config.title_selector)
        if found:
            return " ".join(found[0].text_content().split())

    for selector, attr in (
        ('meta[property="og:title"]', "content"),
        ('meta[name="twitter:title"]', "content"),
        ("h1", None),
        ("title", None),
    ):
        found = tree.cssselect(selector)
        if not found:
            continue
        value = found[0].get(attr, "") if attr else found[0].text_content()
        value = " ".join((value or "").split())
        if value:
            return value
    return ""


def _extract_date(tree: lxml_html.HtmlElement, config: ArticleConfig) -> datetime | None:
    """Publish date from the configured selector, then common meta tags."""
    if config.date_selector:
        found = tree.cssselect(config.date_selector)
        if found:
            raw = found[0].get(config.date_attr) or found[0].text_content()
            parsed = parse_date(raw)
            if parsed:
                return parsed

    for selector, attr in (
        ('meta[property="article:published_time"]', "content"),
        ('meta[name="publish_date"]', "content"),
        ('meta[property="og:published_time"]', "content"),
        ("time[datetime]", "datetime"),
        ("time", None),
    ):
        found = tree.cssselect(selector)
        if not found:
            continue
        raw = found[0].get(attr) if attr else found[0].text_content()
        parsed = parse_date(raw)
        if parsed:
            return parsed
    return None


def _extract_content(
    tree: lxml_html.HtmlElement,
    config: ArticleConfig,
    url: str,
) -> str:
    """Return the article body as a self-contained minimal HTML document.

    Falls back through a list of common content containers, and finally to the
    whole ``<body>``. Falling back is deliberately noisy in the log: a mapping
    whose selector has gone stale still produces output, but says so.
    """
    element = None

    if config.content_selector:
        found = tree.cssselect(config.content_selector)
        if found:
            element = found[0]
        else:
            logger.warning(
                "content_selector %r matched nothing at %s — falling back",
                config.content_selector,
                url,
            )

    if element is None:
        for selector in _FALLBACK_CONTENT_SELECTORS:
            found = tree.cssselect(selector)
            if found:
                logger.info("Using fallback content selector %r for %s", selector, url)
                element = found[0]
                break

    if element is None:
        body = tree.cssselect("body")
        element = body[0] if body else tree
        logger.warning("No content container matched at %s — storing full body", url)

    _strip_noise(element)

    fragment = etree.tostring(element, encoding="unicode", method="html")
    if not fragment.strip():
        raise ExtractionError(f"Extracted an empty content fragment from {url}")

    return _wrap_document(fragment, url)


def _strip_noise(element: lxml_html.HtmlElement) -> None:
    """Drop script/style/noscript subtrees in place."""
    for tag in _STRIP_TAGS:
        for node in element.cssselect(tag):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)


def _wrap_document(fragment: str, url: str) -> str:
    """Wrap a content fragment in a minimal standalone HTML document.

    ``newsletters-web`` copies this file verbatim and loads it in a sandboxed
    iframe, so it needs to stand on its own: a charset declaration (otherwise
    the browser guesses and mangles smart quotes) and a link back to the source.
    """
    escaped_url = html.escape(url, quote=True)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f'<link rel="canonical" href="{escaped_url}">\n'
        "</head>\n"
        "<body>\n"
        f"{fragment}\n"
        "</body>\n"
        "</html>\n"
    )
