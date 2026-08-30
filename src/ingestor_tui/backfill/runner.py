"""Backfill orchestrator: list → classify → fetch → convert → write.

Mirrors ``gmail_ingestor.pipeline.ingestor.EmailIngestor``: a synchronous,
blocking runner with an ``on_progress`` callback and a ``should_stop``
predicate, so the TUI can drive it from a worker thread exactly as it drives
the Gmail pipeline.

Two stages, both independently useful:

``scan()``  Read the archive and classify every entry against the corpus.
            Purely read-only — no files, no database writes. This is what the
            operator looks at before committing to a run.
``run()``   Everything scan does, then fetch and write the missing articles.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from gmail_ingestor.config.settings import GmailIngestorSettings

from ingestor_tui.backfill.extractor import extract_article
from ingestor_tui.backfill.fetcher import DEFAULT_DELAY_SECONDS, Fetcher
from ingestor_tui.backfill.listing import read_listing
from ingestor_tui.backfill.mappings import BackfillMapping, MappingStore
from ingestor_tui.backfill.matcher import (
    CorpusIndex,
    HeldMessage,
    classify,
    index_from_markdown,
)
from ingestor_tui.backfill.models import BackfillProgress, BackfillResult, ScanEntry
from ingestor_tui.backfill.store import BackfillTracker
from ingestor_tui.backfill.writer import BackfillWriter
from ingestor_tui.db_reader import DBReader

logger = logging.getLogger(__name__)


class BackfillError(RuntimeError):
    """Raised when a backfill cannot proceed."""


class BackfillRunner:
    """Runs archive backfill for one label at a time."""

    def __init__(
        self,
        settings: GmailIngestorSettings,
        *,
        mapping_store: MappingStore | None = None,
        on_progress: Callable[[BackfillProgress], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        respect_robots: bool = True,
    ) -> None:
        self._settings = settings
        self._mappings = mapping_store or MappingStore()
        self._on_progress = on_progress
        self._should_stop = should_stop or (lambda: False)
        self._delay_seconds = delay_seconds
        self._respect_robots = respect_robots
        self._db = DBReader(settings.database_path)

    # --- public API ---

    def scan(self, label_name: str, *, limit: int | None = None) -> list[ScanEntry]:
        """Classify every archive entry for a label. Performs no writes."""
        mapping = self._mappings.get(label_name)
        progress = BackfillProgress(label=label_name, current_stage="listing")
        self._notify(progress)

        with Fetcher(
            delay_seconds=self._delay_seconds, respect_robots=self._respect_robots
        ) as fetcher:
            refs = read_listing(mapping, fetcher, limit=limit)

        progress.articles_listed = len(refs)
        progress.current_stage = "matching"
        self._notify(progress)

        corpus = self._build_corpus(mapping)
        known_urls = self._completed_urls(label_name)
        entries = classify(refs, corpus, known_urls=known_urls)

        progress.articles_missing = sum(1 for e in entries if e.is_missing)
        progress.current_stage = "complete"
        self._notify(progress)
        return entries

    def run(
        self,
        label_name: str,
        *,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> BackfillResult:
        """Backfill every missing article for a label.

        Args:
            label_name: Label to backfill; must have a mapping entry.
            limit: Cap the number of articles *written* (not listed).
            dry_run: Classify and report without touching disk or the database.
        """
        mapping = self._mappings.get(label_name)
        entries = self.scan(label_name)
        missing = [e for e in entries if e.is_missing]
        held = len(entries) - len(missing)

        if limit is not None:
            missing = missing[:limit]

        logger.info(
            "%s: %d listed, %d already held, %d to backfill%s",
            label_name, len(entries), held, len(missing), " (dry run)" if dry_run else "",
        )

        if dry_run:
            return BackfillResult(
                label=label_name, listed=len(entries), already_held=held,
                selected=len(missing), written=0, failed=0, dry_run=True,
                entries=tuple(entries),
            )

        return self._write_missing(mapping, entries, missing, held)

    # --- internals ---

    def _write_missing(
        self,
        mapping: BackfillMapping,
        entries: list[ScanEntry],
        missing: list[ScanEntry],
        held: int,
    ) -> BackfillResult:
        """Fetch and persist the missing articles, recording state as we go."""
        self._settings.ensure_directories()
        writer = BackfillWriter(
            self._settings.output_markdown_dir, self._settings.output_raw_dir
        )

        progress = BackfillProgress(
            label=mapping.label_name,
            articles_listed=len(entries),
            articles_missing=len(missing),
            current_stage="fetching",
        )

        written = failed = 0

        with BackfillTracker.beside(self._settings.database_path) as tracker, Fetcher(
            delay_seconds=self._delay_seconds, respect_robots=self._respect_robots
        ) as fetcher:
            run_id = tracker.start_run(mapping.label_name)

            # Held entries are recorded too, so a later run can explain why an
            # article was skipped without redoing the title matching.
            for entry in entries:
                if not entry.is_missing:
                    self._record(tracker, mapping, entry)

            for entry in missing:
                # Checked between articles rather than mid-write, so a stop
                # never leaves a markdown file without its raw body.
                if self._should_stop():
                    logger.info("Stop requested — halting after %d articles", written)
                    break

                progress.current_title = entry.ref.title
                self._notify(progress)
                self._record(tracker, mapping, entry)

                try:
                    article = extract_article(entry.ref, mapping.article, fetcher)
                    result = writer.write(entry.article_id, article, mapping)
                    tracker.mark_done(
                        entry.article_id,
                        raw_html_path=str(result.raw_html_path),
                        markdown_path=str(result.markdown_path),
                    )
                    written += 1
                    progress.articles_written = written
                except Exception as e:
                    # One bad article must not end the run — a 404 on a
                    # withdrawn post is normal for an old archive.
                    logger.warning("Failed to backfill %s: %s", entry.ref.url, e)
                    tracker.mark_failed(entry.article_id, str(e))
                    failed += 1
                    progress.articles_failed = failed

                self._notify(progress)

            tracker.complete_run(
                run_id,
                articles_listed=len(entries),
                articles_held=held,
                articles_written=written,
                articles_failed=failed,
            )

        progress.current_stage = "complete"
        progress.current_title = ""
        self._notify(progress)

        return BackfillResult(
            label=mapping.label_name, listed=len(entries), already_held=held,
            selected=len(missing), written=written, failed=failed,
            dry_run=False, entries=tuple(entries),
        )

    @staticmethod
    def _record(
        tracker: BackfillTracker, mapping: BackfillMapping, entry: ScanEntry
    ) -> None:
        """Persist an entry's classification before acting on it."""
        published = entry.ref.published_at.isoformat() if entry.ref.published_at else ""
        tracker.record_article(
            article_id=entry.article_id,
            label_name=mapping.label_name,
            label_id=mapping.label_id,
            url=entry.ref.url,
            title=entry.ref.title,
            published_at=published,
            status=entry.status,
            match_reason=entry.match_reason,
        )

    def _build_corpus(self, mapping: BackfillMapping) -> CorpusIndex:
        """Index what we already hold, preferring the database.

        Falls back to scanning output markdown only when the DB gives nothing —
        an empty result there is more likely a stale label_id than a genuinely
        empty label, and silently backfilling an entire archive we already hold
        would be the worst possible failure mode.
        """
        if mapping.label_id:
            rows = self._db.get_messages_for_label(mapping.label_id)
            if rows:
                logger.info(
                    "Corpus: %d messages from the database for %s",
                    len(rows), mapping.label_name,
                )
                return CorpusIndex(
                    [
                        HeldMessage(
                            message_id=row["message_id"],
                            subject=row.get("subject") or "",
                            date=row.get("date") or "",
                        )
                        for row in rows
                    ]
                )
            logger.warning(
                "No database rows for label_id %r — falling back to markdown scan",
                mapping.label_id,
            )
        else:
            logger.warning("Mapping has no label_id — falling back to markdown scan")

        return index_from_markdown(
            Path(self._settings.output_markdown_dir), mapping.label_name
        )

    def _completed_urls(self, label_name: str) -> set[str]:
        """Canonical URLs already backfilled, or an empty set if none yet."""
        db_path = BackfillTracker.beside(self._settings.database_path).path
        if not db_path.exists():
            return set()
        with BackfillTracker(db_path) as tracker:
            from ingestor_tui.backfill.identity import canonicalize_url

            return {canonicalize_url(url) for url in tracker.completed_urls(label_name)}

    def _notify(self, progress: BackfillProgress) -> None:
        if self._on_progress:
            self._on_progress(progress)
