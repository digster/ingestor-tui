"""Rewrite Substack embeds into plain markup that survives markdown conversion.

Substack renders cross-links as card components: an embedded post, a digest of
"read full story" cards, a recommended publication. The cards reach the stored
HTML intact, but the markdown is produced by trafilatura, whose boilerplate
filter (``OVERALL_DISCARD_XPATH``) drops any element whose class contains
``embed``, ``meta``, ``author``, ``button`` and a few dozen other words — the
exact vocabulary these cards are built from (``embedded-post-wrap``,
``embedded-post-meta``). The result was markdown that kept an author's lead-in
("Ted Mabrey's essay on the FDE model here is good:") and silently lost the
essay it introduced.

Media embeds fail differently: a YouTube or Instagram embed is an ``<iframe>``
with no text at all, so the markdown held no trace that there was a video.

So before extraction continues, each recognised embed is rewritten:

* **Cards are replaced** by a labelled link line — ``Embedded post: <a>title</a>
  — byline`` — plus, for posts, the excerpt the card displayed. Card furniture
  ("2 years ago · 40 likes", "Read more") is dropped: it was stale the day after
  the page was scraped.
* **Media is kept and annotated.** The iframe stays (see ``extractor``: an
  embedded video is evidence of content) and a labelled link to the original is
  added after it.

The replacement markup is shaped by three trafilatura behaviours, each of which
silently loses content if ignored:

1. **No class attributes.** Classes are what the boilerplate filter keys on.
   Provenance goes in ``data-backfill-embed`` instead, which it ignores.
2. **Links never go inside ``<blockquote>``.** ``handle_quotes`` rebuilds a
   quote's children without their attributes, so a link in a quote comes out
   as ``[title]`` with no URL. Only the link-free excerpt is quoted.
3. **Every link line opens with a plain-text label.** ``link_density_test``
   deletes a ``<p>`` shorter than 30–60 characters whose link text exceeds 80%
   of it; a bare ``<p><a>Watch on YouTube</a></p>`` vanishes. A label of 15+
   characters keeps one link below 75% of any paragraph short enough to be
   tested, whatever the title's length. It also tells a reader what used to be
   there.

Embeds are identified by ``data-component-name`` — the name of the component
that rendered them (``EmbeddedPostToDOM``, ``DigestPostEmbed``, …) — and read
from their ``data-attrs`` JSON where the component provides one. Class names
are not used: on newer components they are hashed per build
(``digestPostEmbed-flwiST``).

Anything unreadable is left exactly as served and logged — the failure mode is
the pre-existing behaviour, never less content than before.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
from collections.abc import Callable
from typing import Any

from lxml import html as lxml_html
from lxml.html.builder import BLOCKQUOTE, A, P

logger = logging.getLogger(__name__)

# Set on every element this module inserts, naming the component it came from.
# A data attribute rather than a class, because trafilatura filters on classes.
EMBED_MARKER_ATTR = "data-backfill-embed"

# Labels opening each inserted line. Load-bearing, not decoration: see rule 3
# in the module docstring. Each must stay at least 15 characters with its
# trailing ": ", or a short, credit-less title becomes deletable again.
POST_LABEL = "Embedded post"
PUBLICATION_LABEL = "Embedded publication"
MEDIA_LABEL = "Embedded media"

# Reads one embed and returns the elements to insert, or None when the embed
# cannot be read (it is then left as served).
type _Handler = Callable[[lxml_html.HtmlElement], list[lxml_html.HtmlElement] | None]

_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{6,20}")
_INSTAGRAM_PERMALINK = re.compile(r'data-instgrm-permalink="([^"]+)"')
_HEADINGS_XPATH = ".//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6]"


def rewrite_embeds(element: lxml_html.HtmlElement, url: str) -> int:
    """Rewrite every recognised embed under ``element`` in place.

    Args:
        element: The article's content subtree.
        url: The article URL, for log messages only.

    Returns:
        How many embeds were rewritten.
    """
    rewritten = 0

    # Reverse document order puts descendants before their ancestors, so if a
    # handled embed ever nests inside another, the inner one is rewritten
    # before the outer one's replacement discards it — no handler is handed a
    # node that has already been detached.
    for node in reversed(element.xpath(".//*[@data-component-name]")):
        name = node.get("data-component-name")
        replace = _CARD_HANDLERS.get(name)
        handler = replace or _MEDIA_HANDLERS.get(name)
        if handler is None:
            continue

        new = handler(node)
        if not new:
            logger.warning("Could not read %s embed at %s — leaving it as served", name, url)
            continue

        _mark(new, name)
        if replace is not None:
            _replace(node, new)
        else:
            _insert_after(node, new)
        rewritten += 1

    # Instagram embeds carry no component name: Substack inlines the whole
    # embed document as a data: URL on a bare <iframe>.
    for frame in reversed(element.xpath(".//iframe[starts-with(@src, 'data:')]")):
        new = _instagram_link(frame)
        if not new:
            continue  # some other inline document, not an Instagram post
        _mark(new, "instagram")
        _insert_after(frame, new)
        rewritten += 1

    if rewritten:
        logger.debug("Rewrote %d embed(s) at %s", rewritten, url)
    return rewritten


# --- card handlers: the card is replaced ---


def _embedded_post(node: lxml_html.HtmlElement) -> list[lxml_html.HtmlElement] | None:
    """``EmbeddedPostToDOM`` → linked title with byline, then the excerpt.

    Everything comes from ``data-attrs``, whose ``url`` is canonical — the
    card's own ``href`` carries ``utm_*`` tracking.
    """
    data = _data_attrs(node)
    url = _clean_url(data.get("url"))
    title = _clean(data.get("title"))
    if not (url and title):
        return None

    bylines = data.get("bylines")
    authors = _join_names(
        [b.get("name") for b in bylines if isinstance(b, dict)] if isinstance(bylines, list) else []
    )
    credit = " · ".join(part for part in (authors, _clean(data.get("publication_name"))) if part)

    elements = [_link_line(POST_LABEL, title, url, credit)]
    excerpt = _clean(data.get("truncated_body_text"))
    if excerpt:
        # Quoted, and link-free: a link inside a quote would lose its URL.
        elements.append(BLOCKQUOTE(P(excerpt)))
    return elements


def _digest_post(node: lxml_html.HtmlElement) -> list[lxml_html.HtmlElement] | None:
    """``DigestPostEmbed`` → linked title with authors · date.

    This component has no ``data-attrs`` and only hashed class names, so it is
    read structurally: the first http(s) link is the post, the heading is its
    title (the thumbnail's ``alt`` if there is no heading), and the element
    right after the heading holds the byline and date as separate children.
    """
    url = next((u for a in node.iter("a") if (u := _clean_url(a.get("href")))), "")
    if not url:
        return None

    headings = node.xpath(_HEADINGS_XPATH)
    heading = headings[0] if headings else None
    title = _clean(heading.text_content()) if heading is not None else ""
    if not title:
        title = next((alt for img in node.iter("img") if (alt := _clean(img.get("alt")))), "")
    if not title:
        return None

    credit = ""
    meta = heading.getnext() if heading is not None else None
    if meta is not None:
        # Children are [authors, "·", date]. Joining their own text, rather
        # than the parent's, restores the spacing the markup leaves out
        # ("Dharmesh Ba·October 31, 2023").
        parts = (_clean(child.text_content()) for child in meta if isinstance(child.tag, str))
        credit = " · ".join(part for part in parts if part and part != "·")

    return [_link_line(POST_LABEL, title, url, credit)]


def _embedded_publication(node: lxml_html.HtmlElement) -> list[lxml_html.HtmlElement] | None:
    """``EmbeddedPublicationToDOMWithSubscribe`` → linked name, tagline · author.

    An author recommending another newsletter; the recommendation is content,
    the subscribe box that came with it is not.
    """
    data = _data_attrs(node)
    url = _clean_url(data.get("base_url"))
    name = _clean(data.get("name"))
    if not (url and name):
        return None

    author = _clean(data.get("author_name"))
    details = [_clean(data.get("hero_text")), f"by {author}" if author else ""]
    credit = " · ".join(part for part in details if part)
    return [_link_line(PUBLICATION_LABEL, name, url, credit)]


# --- media handlers: the embed is kept, a link is added after it ---


def _youtube_link(node: lxml_html.HtmlElement) -> list[lxml_html.HtmlElement] | None:
    """``Youtube2ToDOM`` → a watch link, keeping the start time if one was set."""
    data = _data_attrs(node)
    video_id = data.get("videoId")
    if not isinstance(video_id, str) or not _YOUTUBE_ID.fullmatch(video_id):
        return None

    query = {"v": video_id}
    start = data.get("startTime")
    if isinstance(start, (str, int)) and str(start).strip():
        query["t"] = str(start).strip()  # "12s" and 12 are both valid for t=
    href = "https://www.youtube.com/watch?" + urllib.parse.urlencode(query)
    return [_link_line(MEDIA_LABEL, "Watch on YouTube", href)]


def _instagram_link(frame: lxml_html.HtmlElement) -> list[lxml_html.HtmlElement] | None:
    """An Instagram iframe → a link to the post.

    The post's address appears only inside the inlined document, on the
    blockquote's ``data-instgrm-permalink``. Returns None for any data: iframe
    that is not an Instagram embed, which is not an error.
    """
    document = urllib.parse.unquote(frame.get("src") or "")
    match = _INSTAGRAM_PERMALINK.search(document)
    if not match:
        return None
    url = _clean_url(html.unescape(match.group(1)))
    if not url:
        return None
    return [_link_line(MEDIA_LABEL, "View on Instagram", url)]


_CARD_HANDLERS: dict[str, _Handler] = {
    "EmbeddedPostToDOM": _embedded_post,
    "DigestPostEmbed": _digest_post,
    "EmbeddedPublicationToDOMWithSubscribe": _embedded_publication,
}

_MEDIA_HANDLERS: dict[str, _Handler] = {
    "Youtube2ToDOM": _youtube_link,
}


# --- helpers ---


def _link_line(label: str, text: str, url: str, credit: str = "") -> lxml_html.HtmlElement:
    """``<p>label: <a href=url>text</a> — credit</p>``, the shape every embed reduces to."""
    link = A(text, href=url)
    if credit:
        return P(f"{label}: ", link, f" — {credit}")
    return P(f"{label}: ", link)


def _mark(elements: list[lxml_html.HtmlElement], name: str) -> None:
    """Record which component each inserted element came from."""
    for element in elements:
        element.set(EMBED_MARKER_ATTR, name)


def _replace(node: lxml_html.HtmlElement, new: list[lxml_html.HtmlElement]) -> None:
    """Swap ``node`` for ``new``, keeping the text that followed ``node``.

    lxml stores trailing text on the element itself, so removing ``node``
    would take that text with it; it is handed to the last new element.
    """
    for element in new:
        node.addprevious(element)
    new[-1].tail = node.tail
    node.getparent().remove(node)


def _insert_after(node: lxml_html.HtmlElement, new: list[lxml_html.HtmlElement]) -> None:
    """Insert ``new`` directly after ``node``, ahead of ``node``'s trailing text."""
    new[-1].tail = node.tail
    node.tail = None
    for element in reversed(new):
        node.addnext(element)


def _data_attrs(node: lxml_html.HtmlElement) -> dict[str, Any]:
    """The component's ``data-attrs`` JSON, or ``{}`` if absent or unreadable."""
    raw = node.get("data-attrs")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _clean(value: object) -> str:
    """Whitespace-normalised string, or ``""`` for anything that is not one."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def _clean_url(value: object) -> str:
    """An absolute http(s) URL with ``utm_*`` parameters removed, else ``""``.

    Rejecting other schemes matters: these values come from page JSON, and a
    ``javascript:`` URL must not become a link in the stored document.
    """
    if not isinstance(value, str):
        return ""
    url = value.strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""

    params = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    kept = [(key, val) for key, val in params if not key.lower().startswith("utm_")]
    if len(kept) == len(params):
        return url  # untouched: avoid re-encoding a query we did not change
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(kept)))


def _join_names(names: list[object]) -> str:
    """``["A"]`` → ``"A"``; ``["A", "B"]`` → ``"A and B"``; more → ``"A, B and C"``."""
    cleaned = [name for raw in names if (name := _clean(raw))]
    if len(cleaned) <= 1:
        return "".join(cleaned)
    return f"{', '.join(cleaned[:-1])} and {cleaned[-1]}"
