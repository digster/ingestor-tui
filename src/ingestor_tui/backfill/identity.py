"""Stable identifiers for backfilled articles.

A backfilled article has no Gmail message ID, so one is derived from its
canonical URL. The format is constrained by three downstream consumers, all of
which were checked before it was chosen:

* ``ingestor-tools`` recovers the ID with ``stem.rsplit("_", 1)[-1]`` on the
  markdown filename — so the ID must contain **no underscore**.
* ``newsletter_organizer.find_raw_files`` refuses IDs containing ``/``, ``\\``,
  ``.`` or ``..`` — a hyphen is fine.
* ``newsletters-web`` uses the ID as a directory name and treats it as opaque.

The ``web-`` prefix also keeps backfilled items visibly distinct from the
16-hex Gmail IDs, which matters when reading a label folder by eye.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

ID_PREFIX = "web-"
ID_HASH_LENGTH = 16


def canonicalize_url(url: str) -> str:
    """Normalise a URL so trivial variants hash to the same article ID.

    Drops the fragment and any query string, lowercases scheme and host, and
    strips a trailing slash. Query strings are dropped deliberately: archive
    listings routinely append tracking parameters (``?utm_source=``,
    ``?r=``) that would otherwise mint a second ID for an article we hold.
    """
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def article_id_for(url: str) -> str:
    """Return the deterministic ``web-<16 hex>`` ID for an article URL."""
    canonical = canonicalize_url(url)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{ID_PREFIX}{digest[:ID_HASH_LENGTH]}"


def is_backfill_id(article_id: str) -> bool:
    """True if an ID was minted by backfill rather than the Gmail pipeline."""
    return article_id.startswith(ID_PREFIX)
