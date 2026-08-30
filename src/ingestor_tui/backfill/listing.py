"""Archive listing readers — turn a mapping into a list of ArticleRefs.

Three modes, chosen per-publication when the mapping is authored:

``html``      Static markup + CSS selectors. The default; works for Ghost,
              Jekyll, WordPress and anything else server-rendered.
``json``      A listing endpoint returning JSON. Needed whenever the HTML is
              client-rendered — Substack, beehiiv, the Ghost Content API.
``rendered``  Headless browser with scroll-to-bottom, then the ``html``
              selectors. Escape hatch for JS-only archives with no endpoint;
              requires the optional ``rendered`` extra.

Every mode funnels through ``_paginate``, which owns the single most important
invariant here: **stop when a page yields no new URLs.** Substack's archive
accepts ``?offset=N`` and silently returns page 1 again, so a naive loop would
either spin to max_pages collecting duplicates or, worse, convince the caller
the archive was fully read when only the first page had been seen.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from lxml import html as lxml_html

from ingestor_tui.backfill.fetcher import Fetcher
from ingestor_tui.backfill.mappings import BackfillMapping, ListingConfig
from ingestor_tui.backfill.models import ArticleRef
from ingestor_tui.backfill.parsing import dotted_get, parse_date, select_field

logger = logging.getLogger(__name__)


class ListingError(RuntimeError):
    """Raised when an archive listing cannot be read."""


def read_listing(
    mapping: BackfillMapping,
    fetcher: Fetcher,
    *,
    limit: int | None = None,
) -> list[ArticleRef]:
    """Enumerate every article in a publication's archive, newest first.

    Args:
        mapping: The validated mapping entry for this label.
        fetcher: Polite HTTP client.
        limit: Stop once this many unique articles have been collected.

    Returns:
        Deduplicated ArticleRefs in listing order.
    """
    cfg = mapping.listing
    reader = _READERS.get(cfg.mode)
    if reader is None:  # pragma: no cover — ListingConfig validates the mode
        raise ListingError(f"Unknown listing mode {cfg.mode!r}")

    seen: set[str] = set()
    refs: list[ArticleRef] = []

    for page_refs in reader(cfg, fetcher):
        new_on_page = 0
        for ref in page_refs:
            if not ref.url or ref.url in seen:
                continue
            seen.add(ref.url)
            refs.append(ref)
            new_on_page += 1
            if limit is not None and len(refs) >= limit:
                logger.info("Listing limit of %d reached", limit)
                return refs

        # The guard described in the module docstring. An empty page and a
        # page of entirely-duplicate URLs are the same signal: we are not
        # advancing, so keep going and we would loop to max_pages for nothing.
        if new_on_page == 0:
            logger.debug("Page yielded no new URLs — end of listing")
            break

    logger.info("Listing produced %d unique articles", len(refs))
    return refs


# --- page URL generation -----------------------------------------------------


def _page_urls(cfg: ListingConfig) -> Iterator[str]:
    """Yield successive listing page URLs according to the pagination config."""
    pagination = cfg.pagination

    if pagination.type == "none":
        yield cfg.url_template
        return

    for index in range(pagination.max_pages):
        if pagination.type == "offset":
            offset = pagination.start + index * pagination.page_size
            yield cfg.url_template.format(offset=offset, limit=pagination.page_size)
        else:  # "page"
            yield cfg.url_template.format(
                page=pagination.start + index, limit=pagination.page_size
            )


# --- mode: json --------------------------------------------------------------


def _read_json(cfg: ListingConfig, fetcher: Fetcher) -> Iterator[list[ArticleRef]]:
    """Read a JSON listing endpoint, one page per request."""
    for url in _page_urls(cfg):
        payload = fetcher.get_json(url)
        items = dotted_get(payload, cfg.items_path)

        if items is None:
            logger.warning("items_path %r matched nothing at %s", cfg.items_path, url)
            return
        if not isinstance(items, list):
            raise ListingError(
                f"items_path {cfg.items_path!r} resolved to {type(items).__name__}, expected a list"
            )
        if not items:
            return

        yield [_ref_from_json(item, cfg.fields) for item in items]


def _ref_from_json(item: Any, fields: dict[str, Any]) -> ArticleRef:
    """Build an ArticleRef from one JSON listing item via dotted field paths."""
    url = dotted_get(item, str(fields.get("url", "")))
    title = dotted_get(item, str(fields.get("title", "")))
    raw_date = dotted_get(item, str(fields.get("date", ""))) if fields.get("date") else None
    return ArticleRef(
        url=str(url or "").strip(),
        title=str(title or "").strip(),
        published_at=parse_date(raw_date if isinstance(raw_date, str) else None),
    )


# --- mode: html --------------------------------------------------------------


def _read_html(cfg: ListingConfig, fetcher: Fetcher) -> Iterator[list[ArticleRef]]:
    """Read server-rendered listing pages with CSS selectors.

    When ``next_page_selector`` is set it takes precedence over the templated
    page URLs — following the site's own "older posts" link is more reliable
    than guessing its pagination scheme.
    """
    if cfg.next_page_selector:
        yield from _follow_next_links(cfg, fetcher)
        return

    for url in _page_urls(cfg):
        markup = fetcher.get_text(url)
        refs = _refs_from_markup(markup, cfg, url)
        if not refs:
            return
        yield refs


def _follow_next_links(cfg: ListingConfig, fetcher: Fetcher) -> Iterator[list[ArticleRef]]:
    """Walk a listing by repeatedly following its next-page link."""
    url = cfg.url_template
    visited: set[str] = set()

    for _ in range(cfg.pagination.max_pages):
        if not url or url in visited:
            return
        visited.add(url)

        markup = fetcher.get_text(url)
        refs = _refs_from_markup(markup, cfg, url)
        if not refs:
            return
        yield refs

        tree = _parse(markup, url)
        found = tree.cssselect(cfg.next_page_selector)
        url = found[0].get("href", "") if found else ""


def _refs_from_markup(markup: str, cfg: ListingConfig, base_url: str) -> list[ArticleRef]:
    """Apply the item selector and field specs to one page of markup."""
    tree = _parse(markup, base_url)
    items = tree.cssselect(cfg.item_selector)
    if not items:
        logger.debug("item_selector %r matched nothing at %s", cfg.item_selector, base_url)
        return []

    refs: list[ArticleRef] = []
    for item in items:
        url = select_field(item, cfg.fields.get("url"), base_url)
        title = select_field(item, cfg.fields.get("title"), base_url)
        raw_date = ""
        if cfg.fields.get("date"):
            raw_date = select_field(item, cfg.fields["date"], base_url)
        refs.append(ArticleRef(url=url, title=title, published_at=parse_date(raw_date)))
    return refs


def _parse(markup: str, base_url: str) -> lxml_html.HtmlElement:
    """Parse markup into an lxml tree with its base URL recorded."""
    try:
        tree = lxml_html.fromstring(markup)
    except Exception as e:  # lxml raises a variety of parser errors
        raise ListingError(f"Could not parse HTML from {base_url}: {e}") from e
    tree.make_links_absolute(base_url, resolve_base_href=True)
    return tree


# --- mode: rendered ----------------------------------------------------------


def _read_rendered(cfg: ListingConfig, fetcher: Fetcher) -> Iterator[list[ArticleRef]]:
    """Load the archive in a headless browser, scroll it, then apply selectors.

    Only the first templated URL is used: an infinite-scroll archive is one
    page by definition, and scrolling is what reveals the rest of it.
    """
    markup = _render_page(cfg)
    refs = _refs_from_markup(markup, cfg, cfg.url_template)
    if refs:
        yield refs


def _render_page(cfg: ListingConfig) -> str:
    """Return the fully-scrolled HTML of the archive page.

    Playwright is imported lazily so the base install stays light and the
    failure mode is an actionable message rather than a startup ImportError.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ListingError(
            "Listing mode 'rendered' needs Playwright. Install it with:\n"
            "  uv sync --extra rendered && uv run playwright install chromium"
        ) from e

    from ingestor_tui.backfill.fetcher import USER_AGENT

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(cfg.url_template, wait_until="networkidle")

            # Scroll until the document stops growing. Height is the signal
            # rather than a fixed scroll count, so short archives finish fast
            # and long ones are not cut off early.
            previous_height = 0
            for _ in range(cfg.max_scrolls):
                page.mouse.wheel(0, 20000)
                page.wait_for_timeout(cfg.scroll_wait_ms)
                height = page.evaluate("document.body.scrollHeight")
                if height == previous_height:
                    break
                previous_height = height

            return page.content()
        finally:
            browser.close()


_READERS = {
    "html": _read_html,
    "json": _read_json,
    "rendered": _read_rendered,
}
