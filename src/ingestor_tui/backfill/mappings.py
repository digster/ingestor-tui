"""Load, validate and persist ``backfill_mappings.json``.

One entry per Gmail label, describing where that publication's web archive
lives and how to read it. An entry holds one or more ``ArchiveSource``: a
newsletter can outlive a single archive URL, so the schema accepts either the
single-archive shorthand (``archive_url``/``listing``/``article`` inline) or a
``sources`` list, and normalises both to a tuple. Entries are authored by an LLM (see the
``backfill-mapping`` skill) after probing the archive page, then validated here
before any network work happens.

Follows the same read/write shape as ``ingestor_tui.preset_store.PresetStore``,
but the file is project-local and version-controlled rather than user-level:
a mapping is a fact about a publication, not a user preference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

# Repo root: src/ingestor_tui/backfill/mappings.py → ../../../
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_PATH = _PACKAGE_ROOT / "backfill_mappings.json"

SCHEMA_VERSION = 1

LISTING_MODES = {"html", "json", "rendered"}
PAGINATION_TYPES = {"offset", "page", "none"}

# Field names every listing must yield. ``date`` is optional at the listing
# level because some archives only carry it on the article page itself.
REQUIRED_LISTING_FIELDS = {"url", "title"}


class MappingError(ValueError):
    """Raised when a mapping entry is missing or malformed."""


@dataclass(frozen=True)
class ArticleConfig:
    """Selectors applied to an individual article page."""

    content_selector: str = ""
    title_selector: str = ""
    date_selector: str = ""
    date_attr: str = "datetime"
    # Chrome to remove from the extracted content subtree. Empty means "use
    # extractor.DEFAULT_STRIP_SELECTORS" — resolved there rather than here so
    # mappings.py stays free of an import back from extractor.py. Override for
    # a publication whose real content uses <button> or form controls.
    strip_selectors: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArticleConfig:
        strip = data.get("strip_selectors") or ()
        if isinstance(strip, str) or not isinstance(strip, (list, tuple)):
            raise MappingError("article.strip_selectors must be a list of CSS selectors")

        return cls(
            content_selector=str(data.get("content_selector", "")),
            title_selector=str(data.get("title_selector", "")),
            date_selector=str(data.get("date_selector", "")),
            date_attr=str(data.get("date_attr", "datetime")),
            strip_selectors=tuple(str(s) for s in strip),
        )


@dataclass(frozen=True)
class Pagination:
    """How to walk beyond the first page of a listing.

    ``type='none'`` means the archive is a single page. ``offset`` advances by
    ``page_size`` per request; ``page`` increments an integer page number from
    ``start``.
    """

    type: str = "none"
    page_size: int = 50
    start: int = 0
    max_pages: int = 20

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pagination:
        ptype = str(data.get("type", "none"))
        if ptype not in PAGINATION_TYPES:
            raise MappingError(
                f"pagination.type must be one of {sorted(PAGINATION_TYPES)}, got {ptype!r}"
            )
        return cls(
            type=ptype,
            page_size=int(data.get("page_size", 50)),
            start=int(data.get("start", 0)),
            max_pages=int(data.get("max_pages", 20)),
        )


@dataclass(frozen=True)
class ListingConfig:
    """How to enumerate article URLs from the archive."""

    mode: str
    url_template: str
    pagination: Pagination
    # html / rendered
    item_selector: str = ""
    next_page_selector: str = ""
    # json
    items_path: str = ""
    # mode-dependent: {"url": {...}|"dotted.path", ...}
    fields: dict[str, Any] = None  # type: ignore[assignment]
    # rendered only
    max_scrolls: int = 20
    scroll_wait_ms: int = 800

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ListingConfig:
        mode = str(data.get("mode", "html"))
        if mode not in LISTING_MODES:
            raise MappingError(
                f"listing.mode must be one of {sorted(LISTING_MODES)}, got {mode!r}"
            )

        url_template = str(data.get("url_template", "")).strip()
        if not url_template:
            raise MappingError("listing.url_template is required")

        fields = data.get("fields") or {}
        if not isinstance(fields, dict):
            raise MappingError("listing.fields must be an object")
        missing = REQUIRED_LISTING_FIELDS - set(fields)
        if missing:
            raise MappingError(f"listing.fields is missing {sorted(missing)}")

        scroll = data.get("scroll") or {}

        cfg = cls(
            mode=mode,
            url_template=url_template,
            pagination=Pagination.from_dict(data.get("pagination") or {}),
            item_selector=str(data.get("item_selector", "")),
            next_page_selector=str(data.get("next_page_selector", "")),
            items_path=str(data.get("items_path", "")),
            fields=fields,
            max_scrolls=int(scroll.get("max_scrolls", 20)),
            scroll_wait_ms=int(scroll.get("wait_ms", 800)),
        )

        if mode in ("html", "rendered") and not cfg.item_selector:
            raise MappingError(f"listing.item_selector is required for mode {mode!r}")

        # A templated URL must actually carry the placeholder its pagination
        # type substitutes, or every "page" would refetch page 1 — the exact
        # silent-truncation failure this feature exists to avoid.
        if cfg.pagination.type == "offset" and "{offset}" not in url_template:
            raise MappingError("pagination.type 'offset' requires {offset} in url_template")
        if cfg.pagination.type == "page" and "{page}" not in url_template:
            raise MappingError("pagination.type 'page' requires {page} in url_template")

        return cfg


@dataclass(frozen=True)
class ArchiveSource:
    """One archive a label's articles can be read from.

    A publication's lifetime is not always one URL. Authors rebrand, change
    platform, or move to a new subdomain, and the back catalogue does not
    reliably move with them — see "A rebrand can split one label across two
    archives" in LEARNINGS.md. Each era gets its own source so the whole
    history of a label can be backfilled from one mapping entry.
    """

    archive_url: str
    listing: ListingConfig
    article: ArticleConfig
    # Sending address for articles from this archive. Resolved at parse time:
    # blank here inherits the mapping-level ``sender``. Set it per source when
    # the publication changed address, so backfilled front matter carries the
    # address that era's emails genuinely came from rather than the current one.
    sender: str = ""
    # Human label for logs, the CLI and the TUI — e.g. "Neel's Newsletter
    # (pre-rebrand)". Falls back to the archive host when blank.
    name: str = ""
    notes: str = ""

    @property
    def display_name(self) -> str:
        """Name for operator-facing output, never empty."""
        if self.name:
            return self.name
        from urllib.parse import urlsplit

        return urlsplit(self.archive_url).netloc or self.archive_url

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, context: str) -> ArchiveSource:
        if not isinstance(data, dict):
            raise MappingError(f"{context} must be an object")

        archive_url = str(data.get("archive_url", "")).strip()
        if not archive_url:
            raise MappingError(f"{context}: archive_url is required")
        if not archive_url.startswith(("http://", "https://")):
            raise MappingError(
                f"{context}: archive_url must be http(s), got {archive_url!r}"
            )

        listing = data.get("listing")
        if not isinstance(listing, dict):
            raise MappingError(f"{context}: listing section is required")

        try:
            listing_cfg = ListingConfig.from_dict(listing)
        except MappingError as e:
            raise MappingError(f"{context}: {e}") from e

        return cls(
            archive_url=archive_url,
            listing=listing_cfg,
            article=ArticleConfig.from_dict(data.get("article") or {}),
            sender=str(data.get("sender", "")).strip(),
            name=str(data.get("name", "")).strip(),
            notes=str(data.get("notes", "")),
        )


@dataclass(frozen=True)
class BackfillMapping:
    """A complete mapping entry for one label.

    Holds one or more :class:`ArchiveSource`. ``sources`` is never empty, and
    ``sources[0]`` is the *primary* archive — the one whose articles win when
    two sources list the same post. Author the current/canonical archive first.
    """

    label_name: str
    label_id: str
    sender: str
    sources: tuple[ArchiveSource, ...]
    notes: str = ""

    # --- single-source conveniences -------------------------------------
    # The overwhelmingly common case is one archive, and these keep callers
    # that predate multi-source support (listing.py, cli.py, the TUI) reading
    # naturally instead of indexing into a tuple they do not care about.

    @property
    def archive_url(self) -> str:
        return self.sources[0].archive_url

    @property
    def listing(self) -> ListingConfig:
        return self.sources[0].listing

    @property
    def article(self) -> ArticleConfig:
        return self.sources[0].article

    @property
    def is_multi_source(self) -> bool:
        return len(self.sources) > 1

    def source_at(self, index: int) -> ArchiveSource:
        """Resolve a source by index, falling back to the primary.

        Out-of-range indexes are clamped rather than raised: an index reaches
        here from an ``ArticleRef`` that may have been recorded against an
        older version of the mapping, and backfilling such an article against
        the primary archive's selectors beats crashing the run.
        """
        if 0 <= index < len(self.sources):
            return self.sources[index]
        return self.sources[0]

    @classmethod
    def from_dict(cls, label_name: str, data: dict[str, Any]) -> BackfillMapping:
        if not isinstance(data, dict):
            raise MappingError(f"mapping for {label_name!r} must be an object")

        mapping_sender = str(data.get("sender", "")).strip()
        raw_sources = data.get("sources")
        has_inline = bool(str(data.get("archive_url", "")).strip())

        # The two spellings are mutually exclusive. Accepting both would leave
        # it ambiguous which archive is primary, and silently ignoring one is
        # precisely the class of quiet truncation this subsystem guards against.
        if raw_sources is not None and has_inline:
            raise MappingError(
                f"{label_name}: use either a top-level archive_url or a 'sources' "
                "list, not both"
            )

        if raw_sources is not None:
            if not isinstance(raw_sources, list) or not raw_sources:
                raise MappingError(f"{label_name}: 'sources' must be a non-empty list")
            sources = tuple(
                ArchiveSource.from_dict(entry, context=f"{label_name} source[{i}]")
                for i, entry in enumerate(raw_sources)
            )
        else:
            # Legacy single-archive shape: the archive fields sit inline on the
            # mapping. Normalised to a one-element tuple so everything
            # downstream handles exactly one shape.
            #
            # ``notes`` and ``name`` are cleared on the way in: in this shape
            # they describe the *mapping*, and letting the lone source inherit
            # them would print the same prose twice wherever both are shown.
            sources = (
                replace(
                    ArchiveSource.from_dict(data, context=label_name), notes="", name=""
                ),
            )

        # Resolve the sender fallback once, here, so no consumer has to know
        # that a blank per-source sender means "inherit".
        sources = tuple(
            src if src.sender else replace(src, sender=mapping_sender) for src in sources
        )

        duplicates = _duplicate_archive_urls(sources)
        if duplicates:
            raise MappingError(
                f"{label_name}: duplicate archive_url in sources: {', '.join(duplicates)}"
            )

        return cls(
            label_name=label_name,
            label_id=str(data.get("label_id", "")).strip(),
            sender=mapping_sender,
            sources=sources,
            notes=str(data.get("notes", "")),
        )


def _duplicate_archive_urls(sources: tuple[ArchiveSource, ...]) -> list[str]:
    """Archive URLs appearing more than once, in first-seen order."""
    seen: set[str] = set()
    dupes: list[str] = []
    for src in sources:
        key = src.archive_url.rstrip("/").lower()
        if key in seen and src.archive_url not in dupes:
            dupes.append(src.archive_url)
        seen.add(key)
    return dupes


class MappingStore:
    """Read/write access to the backfill mapping file."""

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def _read_raw(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"version": SCHEMA_VERSION, "mappings": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise MappingError(f"{self._path} is not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise MappingError(f"{self._path} must contain a JSON object")
        return data

    def list_mappings(self) -> dict[str, BackfillMapping]:
        """Return every valid mapping, keyed by label name.

        Raises MappingError on the first malformed entry rather than silently
        dropping it — a mapping that scraped nothing because of a typo is worse
        than one that refuses to load.
        """
        raw = self._read_raw()
        entries = raw.get("mappings") or {}
        if not isinstance(entries, dict):
            raise MappingError("'mappings' must be an object keyed by label name")
        return {
            name: BackfillMapping.from_dict(name, entry) for name, entry in sorted(entries.items())
        }

    def get(self, label_name: str) -> BackfillMapping:
        """Return one mapping by label name, or raise with the available names."""
        mappings = self.list_mappings()
        if label_name not in mappings:
            available = ", ".join(sorted(mappings)) or "(none)"
            raise MappingError(
                f"No backfill mapping for label {label_name!r}. Available: {available}"
            )
        return mappings[label_name]

    def save(self, label_name: str, entry: dict[str, Any]) -> None:
        """Add or replace one mapping entry, validating before it is written."""
        BackfillMapping.from_dict(label_name, entry)  # raises on bad input
        raw = self._read_raw()
        raw.setdefault("version", SCHEMA_VERSION)
        raw.setdefault("mappings", {})
        raw["mappings"][label_name] = entry
        self._write(raw)

    def delete(self, label_name: str) -> None:
        """Remove a mapping entry (no-op if absent)."""
        raw = self._read_raw()
        (raw.get("mappings") or {}).pop(label_name, None)
        self._write(raw)

    def _write(self, raw: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
