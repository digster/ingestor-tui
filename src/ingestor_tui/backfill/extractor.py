"""Article page → title, publish date, and a clean, self-contained HTML document.

The output of this module is what lands in ``../output/raw/{id}.html`` — and that
file, not the markdown sidecar, is what a reader actually sees. ``newsletters-web``
picks ``sorted(glob("*.html"))[0]`` as the article body and loads it in
``<iframe sandbox="allow-same-origin">`` whose only style is a white background.
No host CSS reaches the article.

Which is why this module stores a *styled document* rather than a bare fragment.
An ingested email survives that bare iframe because mail clients strip external
stylesheets, so senders are forced to inline everything — an email is
self-describing by necessity. A web page is the inverse: semantic markup whose
styling lives in external CDN stylesheets we neither fetch nor could usefully
store. Keeping only the content subtree preserved the styling *reference* and
lost the styling itself, which is what made backfilled articles render at
browser defaults while the emails beside them looked right.

So the pipeline here is: narrow to the content subtree (full site chrome would
be noise in a viewer, and trafilatura produces markedly better text from a
narrowed subtree), strip the interactive chrome that subtree carries, then
supply our own fallback stylesheet.
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

# Stripped from the stored fragment unless a mapping overrides it.
#
# script/style/noscript/template: cannot help a reader, and <style> in
# particular refers to a stylesheet we did not capture.
# button/[role=button]/form controls: always site chrome — restack, expand,
# heading-anchor and subscribe widgets. Inert here anyway, since the viewer's
# iframe carries no `allow-scripts`.
#
# Deliberately NOT stripped: `svg` (a standalone one may be a real diagram;
# every icon observed so far is a button child and goes with its button) and
# `iframe` (an embedded video is evidence of content, even if the sandbox
# stops it playing).
DEFAULT_STRIP_SELECTORS = (
    "script",
    "style",
    "noscript",
    "template",
    "button",
    '[role="button"]',
    "form",
    "input",
    "select",
    "textarea",
)

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

# Markers that a page served a preview rather than the article. Used only to
# add a footer pointing at the source — a truncated post is still worth
# archiving for its title and opening lines.
_PAYWALL_SELECTORS = (
    "div.paywall",
    ".paywall-jump",
    '[data-component-name*="Paywall"]',
)

# Containers worth removing once they hold nothing. Stripping a button tends to
# leave its wrapper divs behind (Substack nests three deep), and an empty block
# element still takes vertical space.
_PRUNABLE_TAGS = frozenset({"div", "span", "section", "figure"})

# Elements that carry meaning without contributing text. A container holding
# one of these is not empty, however blank its ``text_content()`` looks.
_MEANINGFUL_VOID_TAGS = frozenset(
    {
        "img", "picture", "source", "video", "audio", "embed", "object",
        "svg", "canvas", "iframe", "hr", "br", "input",
    }
)

# Mirrors EMAIL_PAGE_CSS in newsletters-web/scripts/build_site.py, which styles
# the pages that build generates for emails that arrived without an HTML part.
# Backfilled articles are the same category — "we generated this page, so we
# supply the CSS" — and should look identical in the viewer. Two repos and no
# shared package, so keep the two copies aligned by hand when either changes.
#
# Light-only on purpose: newsletters-web's `.viewer-frame` is hardcoded to a
# white background and every real HTML email renders light, so a dark variant
# would make these pages the odd ones out.
#
# Element-level selectors only, no hooks into publication class names. The low
# specificity means any inline `style=""` that survived on the scraped page
# still wins: this is a floor, not an override.
ARTICLE_PAGE_CSS = """\
:root { color-scheme: light; }
body {
  margin: 0;
  padding: 2rem 1.5rem 4rem;
  background: #fff;
  color: #1a1a1a;
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
  -webkit-text-size-adjust: 100%;
}
.email-body { max-width: 68ch; margin: 0 auto; }
.email-body > :first-child { margin-top: 0; }
p, ul, ol, blockquote, pre { margin: 0 0 1.15em; }
h1, h2, h3, h4, h5, h6 { margin: 1.8em 0 .6em; line-height: 1.3; font-weight: 600; }
h1 { font-size: 1.5em; } h2 { font-size: 1.3em; } h3 { font-size: 1.15em; }
ul, ol { padding-left: 1.5em; }
li { margin-bottom: .4em; }
a { color: #0b5fff; text-decoration: underline; text-underline-offset: 2px; }
a:hover { color: #0844c2; }
blockquote {
  padding-left: 1em;
  border-left: 3px solid #e0e0e0;
  color: #555;
}
code {
  padding: .15em .35em;
  border-radius: 3px;
  background: #f2f2f2;
  font: .9em/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
pre {
  padding: .9em 1em;
  border-radius: 6px;
  background: #f6f6f6;
  overflow-x: auto;
}
pre code { padding: 0; background: none; }
hr { margin: 2em 0; border: 0; border-top: 1px solid #e0e0e0; }
/* Scraped pages carry sized <img> and <figure> that a mail template never
   would: without these an image renders at its width attribute (1456px on
   Substack) and blows the column open. */
img { max-width: 100%; height: auto; }
figure { margin: 1.5em 0; }
figcaption { margin-top: .5em; font-size: .875em; color: #666; text-align: center; }
table { width: 100%; border-collapse: collapse; margin: 0 0 1.15em; }
th, td { padding: .4em .6em; border: 1px solid #e0e0e0; text-align: left; }
.backfill-note {
  margin: 2em 0 0;
  padding-top: 1em;
  border-top: 1px solid #e0e0e0;
  font-size: .9em;
  color: #666;
}
"""


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

    content_html = _extract_content(tree, config, ref.url, title)

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
    title: str,
) -> str:
    """Return the article body as a self-contained, styled HTML document.

    Falls back through a list of common content containers, and finally to the
    whole ``<body>``. Falling back is deliberately noisy in the log: a mapping
    whose selector has gone stale still produces output, but says so.

    The paywall check runs against the *whole page* rather than the extracted
    element, because the marker is a sibling of the content container.
    """
    paywalled = _detect_paywall(tree)
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

    _strip_noise(element, config.strip_selectors or DEFAULT_STRIP_SELECTORS)
    _drop_empty_containers(element)

    fragment = etree.tostring(element, encoding="unicode", method="html")
    if not fragment.strip():
        raise ExtractionError(f"Extracted an empty content fragment from {url}")

    return _wrap_document(fragment, url, title, paywalled=paywalled)


def _detect_paywall(tree: lxml_html.HtmlElement) -> bool:
    """Whether the page served a subscriber preview instead of the article.

    A truncated post is still archived — the title and opening lines have
    completeness value — but the stored document says so and links to the
    source, rather than presenting two sentences as the whole piece.
    """
    for selector in _PAYWALL_SELECTORS:
        try:
            if tree.cssselect(selector):
                return True
        except Exception:  # pragma: no cover — malformed selector constant
            continue
    return False


def _remove_keeping_tail(node: lxml_html.HtmlElement) -> None:
    """Detach ``node`` while preserving the text that followed it.

    lxml stores the text *after* an element on that element, so a plain
    ``parent.remove(node)`` silently eats it — enough to swallow half a
    sentence when the node is an inline button mid-paragraph.
    """
    parent = node.getparent()
    if parent is None:
        return

    tail = node.tail
    if tail:
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail

    parent.remove(node)


def _strip_noise(
    element: lxml_html.HtmlElement,
    selectors: tuple[str, ...],
) -> None:
    """Drop chrome subtrees in place, matched by CSS selector."""
    for selector in selectors:
        try:
            matches = element.cssselect(selector)
        except Exception as e:
            logger.warning("Ignoring unusable strip selector %r: %s", selector, e)
            continue
        for node in matches:
            _remove_keeping_tail(node)


def _drop_empty_containers(element: lxml_html.HtmlElement) -> None:
    """Remove wrapper elements left holding nothing after stripping.

    Walks in reverse document order, which puts children before their parents,
    so a wrapper emptied by this pass is itself removed on the same pass.
    """
    for node in reversed(list(element.iter())):
        if node is element or not isinstance(node.tag, str):
            continue
        if node.tag not in _PRUNABLE_TAGS:
            continue
        if node.text_content().strip():
            continue
        if any(
            child.tag in _MEANINGFUL_VOID_TAGS
            for child in node.iter()
            if isinstance(child.tag, str)
        ):
            continue
        _remove_keeping_tail(node)


def _wrap_document(
    fragment: str,
    url: str,
    title: str,
    *,
    paywalled: bool = False,
) -> str:
    """Wrap a content fragment in a self-contained, styled HTML document.

    ``newsletters-web`` copies this file verbatim and loads it in a sandboxed
    iframe that supplies no CSS of its own, so everything the page needs to
    render has to be in here: a charset (otherwise the browser guesses and
    mangles smart quotes), a viewport, and the fallback stylesheet.

    No ``<h1>``/subject header, matching ``build_site.render_email_page``: the
    viewer chrome already shows the subject, and ``app.js`` builds each list
    preview by walking this document's ``<body>`` text, so a heading here would
    prepend duplicate subject text to every preview row. ``<title>`` lives in
    ``<head>`` and is safe. A *footer* is likewise safe — previews truncate
    from the start.
    """
    escaped_url = html.escape(url, quote=True)
    note = ""
    if paywalled:
        note = (
            '<p class="backfill-note">Subscriber-only post — this is the public '
            f'preview. The full text is at <a href="{escaped_url}">{escaped_url}</a>.</p>\n'
        )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{html.escape(title)}</title>\n"
        f'<link rel="canonical" href="{escaped_url}">\n'
        f"<style>\n{ARTICLE_PAGE_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        '<div class="email-body">\n'
        f"{fragment}\n"
        f"{note}"
        "</div>\n"
        "</body>\n"
        "</html>\n"
    )
