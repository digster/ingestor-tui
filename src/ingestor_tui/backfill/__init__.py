"""Backfill missing newsletter articles from a publication's web archive.

Gmail is the only source the ingestor has, so anything that never arrived as an
email — a back catalogue we subscribed too late for, posts an author published
after migrating platforms — is permanently absent from the corpus. Backfill
closes those gaps by reading the publication's own archive and writing the
missing articles into ../output/ in exactly the shape the Gmail pipeline
produces, so the downstream organizer and site builder need no changes.
"""

from ingestor_tui.backfill.mappings import BackfillMapping, MappingError, MappingStore
from ingestor_tui.backfill.models import (
    ArticleRef,
    BackfillProgress,
    BackfillResult,
    ScanEntry,
)
from ingestor_tui.backfill.runner import BackfillError, BackfillRunner
from ingestor_tui.backfill.store import BackfillTracker

__all__ = [
    "ArticleRef",
    "BackfillError",
    "BackfillMapping",
    "BackfillProgress",
    "BackfillResult",
    "BackfillRunner",
    "BackfillTracker",
    "MappingError",
    "MappingStore",
    "ScanEntry",
]
