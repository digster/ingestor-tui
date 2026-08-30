"""Small shared parsing helpers: dates, dotted JSON paths, CSS extraction."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from lxml import html as lxml_html

logger = logging.getLogger(__name__)

# strptime fallbacks, tried in order after the ISO and RFC-2822 parsers.
# Covers the human-readable stamps that appear in rendered archive listings.
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
)


def parse_date(value: str | None) -> datetime | None:
    """Parse an arbitrary published-date string into an aware UTC datetime.

    Returns None rather than raising: a missing or unparseable listing date is
    recoverable (the article page usually carries one too), so callers decide
    what to do about it.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    # ISO 8601, including the trailing "Z" that JSON APIs emit.
    try:
        return _to_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        pass

    # RFC 2822, as used by RSS <pubDate>.
    try:
        return _to_utc(parsedate_to_datetime(text))
    except (TypeError, ValueError):
        pass

    for fmt in _DATE_FORMATS:
        try:
            return _to_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue

    logger.debug("Could not parse date %r", text)
    return None


def _to_utc(value: datetime) -> datetime:
    """Attach UTC to a naive datetime, or convert an aware one into UTC.

    Everything downstream formats with ``%Y-%m-%d %H:%M:%S`` and drops the zone,
    so normalising here is what keeps a backfilled ``date:`` comparable with an
    ingested one.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def dotted_get(data: Any, path: str) -> Any:
    """Resolve a dotted path such as ``"post.canonical_url"`` inside JSON.

    An empty path returns ``data`` unchanged, which is how a listing endpoint
    that returns a bare array at the root is addressed. Numeric segments index
    into lists. Returns None if any segment is missing.
    """
    if not path:
        return data
    current = data
    for segment in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            if not segment.isdigit():
                return None
            index = int(segment)
            current = current[index] if index < len(current) else None
        elif isinstance(current, dict):
            current = current.get(segment)
        else:
            return None
    return current


def select_field(element: lxml_html.HtmlElement, spec: Any, base_url: str = "") -> str:
    """Pull one field out of a listing item using a mapping field spec.

    ``spec`` is either a bare CSS selector string (implying the element's text)
    or an object ``{"selector": ..., "attr": ...}`` where ``attr`` is an
    attribute name or the literal ``"text"``. An empty ``selector`` addresses
    the item element itself, which is what an ``<a class="post">`` wrapper needs.
    """
    if isinstance(spec, str):
        selector, attr = spec, "text"
    elif isinstance(spec, dict):
        selector = str(spec.get("selector", ""))
        attr = str(spec.get("attr", "text"))
    else:
        return ""

    target = element
    if selector:
        found = element.cssselect(selector)
        if not found:
            return ""
        target = found[0]

    if attr == "text":
        return " ".join(target.text_content().split())

    value = target.get(attr)
    if value is None:
        return ""
    value = value.strip()

    # href/src are resolved against the page they were found on so callers
    # never have to care whether a site emits absolute or relative links.
    if attr in ("href", "src") and base_url:
        from urllib.parse import urljoin

        return urljoin(base_url, value)
    return value
