"""Analyse an archive page so a mapping can be authored for it.

This is the input to the ``backfill-mapping`` skill: it dumps everything an LLM
needs to choose a listing mode and write selectors, and — critically — it
reports the *gap* between what the static HTML contains and what the page
claims to hold. That gap is the signal that a selector-only mapping would
silently backfill half an archive, which is the failure this whole module
exists to prevent.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from lxml import html as lxml_html

from ingestor_tui.backfill.fetcher import Fetcher

logger = logging.getLogger(__name__)

# Platform fingerprints: substrings that only appear in that platform's markup.
_PLATFORM_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("substack", ("substackcdn.com", "substack.com/api/v1", "pencraft")),
    ("ghost", ("ghost-sdk", "content/images/size", "gh-head", "/ghost/api/")),
    ("beehiiv", ("beehiiv.com", "beehiiv-", "/api/v2/publications")),
    ("wordpress", ("wp-content", "wp-json", "wp-includes")),
    ("medium", ("cdn-client.medium.com", "medium.com/_/graphql")),
    ("buttondown", ("buttondown.email", "buttondown.com")),
)

# Listing endpoints worth probing once a platform is recognised. Each is a
# template relative to the site root, with {offset}/{limit} placeholders.
_KNOWN_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "substack": ("/api/v1/archive?sort=new&offset={offset}&limit={limit}",),
    "ghost": ("/ghost/api/content/posts/?limit={limit}&page={page}",),
    "wordpress": ("/wp-json/wp/v2/posts?per_page={limit}&page={page}",),
}

# Path segments that mark a link as navigation rather than an article.
_NON_ARTICLE_SEGMENTS = frozenset(
    {"comments", "about", "archive", "tag", "tags", "category", "author", "feed", "subscribe"}
)


@dataclass
class ProbeReport:
    """Everything discovered about an archive page."""

    url: str
    platform: str = "unknown"
    static_article_count: int = 0
    url_patterns: dict[str, int] = field(default_factory=dict)
    sample_articles: list[dict[str, str]] = field(default_factory=list)
    container_candidates: list[dict[str, Any]] = field(default_factory=list)
    time_elements: list[dict[str, str]] = field(default_factory=list)
    feeds: list[dict[str, str]] = field(default_factory=list)
    json_endpoints: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, ensure_ascii=False)


def probe(url: str, fetcher: Fetcher) -> ProbeReport:
    """Fetch an archive page and report how it could be read."""
    report = ProbeReport(url=url)
    markup = fetcher.get_text(url)
    tree = lxml_html.fromstring(markup)
    tree.make_links_absolute(url, resolve_base_href=True)

    report.platform = _fingerprint(markup)
    _collect_links(tree, url, report)
    _collect_containers(tree, report)
    _collect_times(tree, report)
    _collect_feeds(tree, report)
    _probe_endpoints(url, report, fetcher)
    _add_warnings(report)

    return report


def _fingerprint(markup: str) -> str:
    """Identify the publishing platform from markup signatures."""
    lowered = markup.lower()
    for platform, signatures in _PLATFORM_SIGNATURES:
        if any(signature.lower() in lowered for signature in signatures):
            return platform
    return "unknown"


def _collect_links(tree: lxml_html.HtmlElement, url: str, report: ProbeReport) -> None:
    """Bucket same-host links by URL shape to reveal the article pattern."""
    host = urlsplit(url).netloc
    patterns: Counter[str] = Counter()
    article_urls: dict[str, str] = {}

    for anchor in tree.cssselect("a[href]"):
        href = anchor.get("href", "")
        parts = urlsplit(href)
        if parts.netloc and parts.netloc != host:
            continue

        segments = [s for s in parts.path.split("/") if s]
        if not segments:
            continue
        if segments[-1] in _NON_ARTICLE_SEGMENTS:
            continue

        # Generalise the last segment: "/p/some-post" → "/p/{slug}"
        pattern = "/" + "/".join(segments[:-1] + ["{slug}"])
        patterns[pattern] += 1

        clean = href.split("?")[0].split("#")[0]
        if clean not in article_urls:
            text = " ".join(anchor.text_content().split())
            article_urls[clean] = text

    report.url_patterns = dict(patterns.most_common(10))
    report.static_article_count = len(article_urls)
    report.sample_articles = [
        {"url": u, "text": t[:90]} for u, t in list(article_urls.items())[:5]
    ]


def _collect_containers(tree: lxml_html.HtmlElement, report: ProbeReport) -> None:
    """Find repeated elements that each wrap exactly one article link.

    A listing is, structurally, the most-repeated container class whose
    instances hold one link each. Ranking by repetition surfaces the item
    selector without having to understand the site's design system.
    """
    candidates: Counter[str] = Counter()

    for element in tree.cssselect("article, li, div"):
        classes = (element.get("class") or "").split()
        if not classes:
            continue
        links = element.cssselect("a[href]")
        if not 1 <= len(links) <= 4:
            continue
        # Index by the single most distinctive class, which keeps hashed
        # design-system classes (Substack's "pencraft") from dominating.
        distinctive = max(classes, key=len)
        candidates[f"{element.tag}.{distinctive}"] += 1

    report.container_candidates = [
        {"selector": selector, "count": count}
        for selector, count in candidates.most_common(8)
        if count >= 2
    ]


def _collect_times(tree: lxml_html.HtmlElement, report: ProbeReport) -> None:
    """Sample <time> elements — usually the cleanest date source."""
    report.time_elements = [
        {
            "datetime": element.get("datetime", ""),
            "text": " ".join(element.text_content().split())[:40],
        }
        for element in tree.cssselect("time")[:5]
    ]


def _collect_feeds(tree: lxml_html.HtmlElement, report: ProbeReport) -> None:
    """Collect RSS/Atom alternates, a useful cross-check on titles and dates."""
    report.feeds = [
        {"type": link.get("type", ""), "href": link.get("href", "")}
        for link in tree.cssselect('link[rel="alternate"]')
        if "xml" in (link.get("type") or "")
    ]


def _probe_endpoints(url: str, report: ProbeReport, fetcher: Fetcher) -> None:
    """Try the known JSON listing endpoints for the detected platform."""
    templates = _KNOWN_ENDPOINTS.get(report.platform, ())
    root = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"

    for template in templates:
        probe_url = urljoin(root, template.format(offset=0, limit=5, page=1))
        try:
            payload = fetcher.get_json(probe_url)
        except Exception as e:
            logger.debug("Endpoint probe failed for %s: %s", probe_url, e)
            continue

        items, items_path = _locate_items(payload)
        if not items:
            continue

        report.json_endpoints.append(
            {
                "url_template": urljoin(root, template),
                "items_path": items_path,
                "item_count": len(items),
                "available_fields": sorted(items[0])[:40] if isinstance(items[0], dict) else [],
                "sample_item": {
                    k: v for k, v in list(items[0].items())[:8] if isinstance(v, str | int)
                }
                if isinstance(items[0], dict)
                else {},
            }
        )


def _locate_items(payload: Any) -> tuple[list[Any], str]:
    """Find the array of listing items in a JSON payload, and its dotted path."""
    if isinstance(payload, list):
        return payload, ""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value, key
    return [], ""


def _add_warnings(report: ProbeReport) -> None:
    """Flag the conditions that should push the author away from mode 'html'."""
    if report.json_endpoints:
        report.warnings.append(
            f"A JSON listing endpoint is available ({report.json_endpoints[0]['url_template']}). "
            "Prefer mode 'json' — it paginates reliably where the HTML may not."
        )

    if report.static_article_count and report.static_article_count <= 15:
        report.warnings.append(
            f"Static HTML exposes only {report.static_article_count} articles. If the archive "
            "holds more, this page is client-rendered: mode 'html' would silently backfill "
            "just the first page. Use 'json' if an endpoint exists, otherwise 'rendered'."
        )

    if not report.container_candidates:
        report.warnings.append(
            "No repeated link containers found in the static HTML — the listing is almost "
            "certainly rendered client-side."
        )


def verify_pagination(url_template: str, fetcher: Fetcher) -> dict[str, Any]:
    """Check whether a templated listing URL actually paginates.

    Substack accepts ``?offset=12`` on its HTML archive and returns page 1
    again. Confirming that page 2 differs from page 1 is the cheapest way to
    catch that class of mapping bug before it is committed.
    """
    def slugs_at(offset: int) -> set[str]:
        page = fetcher.get_text(url_template.format(offset=offset, limit=12, page=offset // 12 + 1))
        return set(re.findall(r'href="([^"]+/p/[^"?#]+)"', page))

    first, second = slugs_at(0), slugs_at(12)
    return {
        "page_1_count": len(first),
        "page_2_count": len(second),
        "new_on_page_2": len(second - first),
        "paginates": bool(second - first),
    }
