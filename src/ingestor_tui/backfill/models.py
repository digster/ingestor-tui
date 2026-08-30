"""Frozen dataclasses for the backfill domain model.

Mirrors the shape of ``gmail_ingestor.core.models`` so the two pipelines read
alike: immutable value objects for data, one mutable progress tracker for UIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Status state machine for a backfilled article, stored in backfill.db.
#
#                    ┌─ have  (already in the Gmail corpus — never fetched)
#   discovered ──────┤
#                    └─ done  (fetched, converted, written)
#                          │
#                          └─ failed  (fetch/extract/convert error)
VALID_STATUSES = {"discovered", "have", "done", "failed"}


@dataclass(frozen=True)
class ArticleRef:
    """One entry from an archive listing, before we know whether we hold it."""

    url: str
    title: str
    published_at: datetime | None = None


@dataclass(frozen=True)
class ScanEntry:
    """An ArticleRef classified against the existing corpus."""

    ref: ArticleRef
    article_id: str
    status: str
    match_reason: str = ""

    @property
    def is_missing(self) -> bool:
        return self.status == "discovered"


@dataclass(frozen=True)
class ExtractedArticle:
    """An article page reduced to the parts we persist."""

    url: str
    title: str
    published_at: datetime
    content_html: str


@dataclass
class BackfillProgress:
    """Mutable progress tracker, passed to the ``on_progress`` callback.

    Deliberately parallels ``gmail_ingestor.core.models.FetchProgress`` so the
    TUI can drive both pipelines through the same call_from_thread plumbing.
    """

    label: str = ""
    articles_listed: int = 0
    articles_missing: int = 0
    articles_written: int = 0
    articles_failed: int = 0
    current_stage: str = "idle"
    current_title: str = ""


@dataclass(frozen=True)
class BackfillResult:
    """Outcome of a completed ``BackfillRunner.run()``."""

    label: str
    listed: int = 0
    already_held: int = 0
    # Missing articles chosen for this run — i.e. after --limit is applied.
    # Distinct from len([e for e in entries if e.is_missing]), which counts
    # every gap in the archive regardless of what this run was asked to do.
    selected: int = 0
    written: int = 0
    failed: int = 0
    dry_run: bool = False
    entries: tuple[ScanEntry, ...] = field(default_factory=tuple)
