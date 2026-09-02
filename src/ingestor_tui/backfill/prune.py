"""Remove backfilled articles from every place a backfill run put them.

Needed because a backfill is not a single-destination write. One article lands
in four places, and only one of them ever self-heals:

    ../output/markdown/{slug}_{id}.md    written by BackfillWriter
    ../output/raw/{id}.html              written by BackfillWriter
    ../newsletters/{label}/{id}/         copied there by ingestor-tools
    backfill.db                          the run's own state

``ingestor-tools``' ``organize()`` **only ever copies, and skips files already
present in the destination** (see its ARCHITECTURE.md). So rewriting a file in
``../output`` does not propagate: the stale copy under ``../newsletters`` wins
forever, and ``newsletters-web`` publishes that. Anything that changes how
backfilled articles are written therefore needs a way to clear the old ones
first — this module — after which a normal ``run`` + organize + build restores
them in the new shape.

Deleting the ``backfill.db`` row is not incidental. ``completed_urls`` reads
``done`` rows to decide what to skip, so a row left behind after its files were
removed would make the article permanently invisible: absent from disk, and
never rediscovered by a scan.
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
from pathlib import Path

from gmail_ingestor.config.settings import GmailIngestorSettings

from ingestor_tui.backfill.identity import is_backfill_id
from ingestor_tui.backfill.store import BackfillTracker

logger = logging.getLogger(__name__)

# Relative to the gmail-ingestor project directory the CLI chdirs into, which
# is where GMAIL_OUTPUT_MARKDOWN_DIR's own "../output/markdown" is resolved
# from. Keeping the same base means the two defaults agree by construction.
DEFAULT_NEWSLETTERS_DIR = Path("../newsletters")

# Statuses a prune owns outright. Deliberately excludes `have`, which asserts
# that a Gmail-ingested message already covers the URL: those rows point at no
# backfilled file, and their `web-` ID is a matching key rather than something
# on disk. Clearing them would widen a destructive command's blast radius to
# rows it cannot act on, and a scan re-derives them for free anyway.
#
# A `have` row that *does* have files on disk is still pruned — see plan_prune.
PRUNABLE_STATUSES = ("done", "failed", "discovered")


@dataclasses.dataclass(frozen=True)
class PruneTarget:
    """One article and every path holding a copy of it."""

    article_id: str
    title: str
    published_at: str
    status: str
    files: tuple[Path, ...]
    directories: tuple[Path, ...]

    @property
    def is_present(self) -> bool:
        """Whether anything is actually on disk for this article."""
        return bool(self.files or self.directories)


@dataclasses.dataclass(frozen=True)
class PruneResult:
    """Outcome of a prune, or of the dry run that previews one."""

    label: str
    targets: tuple[PruneTarget, ...] = ()
    files_removed: int = 0
    directories_removed: int = 0
    rows_removed: int = 0
    dry_run: bool = True


def plan_prune(
    settings: GmailIngestorSettings,
    label_name: str,
    *,
    newsletters_dir: Path = DEFAULT_NEWSLETTERS_DIR,
) -> list[PruneTarget]:
    """Locate every artifact belonging to a label's backfilled articles.

    Reads ``backfill.db`` rather than globbing ``../output`` for ``web-*``: the
    database is the record of what *this* feature created, so an article that
    arrived some other way can never be caught up in a prune. Rows are further
    narrowed to ``PRUNABLE_STATUSES`` — see the note there on why ``have`` is
    excluded.
    """
    markdown_dir = Path(settings.output_markdown_dir)
    raw_dir = Path(settings.output_raw_dir)
    label_dir = Path(newsletters_dir) / label_name

    db_path = BackfillTracker.beside(settings.database_path).path
    if not db_path.exists():
        logger.info("No backfill database at %s — nothing to prune", db_path)
        return []

    with BackfillTracker(db_path) as tracker:
        rows = tracker.articles_for_label(label_name)

    targets: list[PruneTarget] = []
    for row in rows:
        article_id = row["article_id"]

        # Belt and braces. The rows are already scoped to this label and were
        # written by backfill, but a non-web ID here would mean deleting a
        # Gmail-ingested article's files, which nothing can undo.
        if not is_backfill_id(article_id):
            logger.warning("Skipping %r — not a backfill ID", article_id)
            continue

        # Globbed, not constructed: the markdown filename carries a title slug
        # that is not stored anywhere and would have to be re-derived.
        files = list(markdown_dir.glob(f"*_{article_id}.md"))
        for suffix in (".html", ".txt"):
            candidate = raw_dir / f"{article_id}{suffix}"
            if candidate.exists():
                files.append(candidate)

        article_dir = label_dir / article_id
        directories = [article_dir] if article_dir.is_dir() else []

        # Status decides *usually*, but files on disk always win. A row whose
        # status was downgraded to `have` by an older build still owns whatever
        # backfill wrote for it, and skipping it on status alone would orphan
        # those files permanently — no scan looks for them, and nothing else
        # knows they exist.
        if row.get("status") not in PRUNABLE_STATUSES and not (files or directories):
            continue

        targets.append(
            PruneTarget(
                article_id=article_id,
                title=row.get("title") or "",
                published_at=(row.get("published_at") or "")[:10],
                status=row.get("status") or "",
                files=tuple(sorted(files)),
                directories=tuple(directories),
            )
        )

    return targets


def prune_label(
    settings: GmailIngestorSettings,
    label_name: str,
    *,
    newsletters_dir: Path = DEFAULT_NEWSLETTERS_DIR,
    dry_run: bool = True,
) -> PruneResult:
    """Delete a label's backfilled artifacts and forget them in the database.

    Args:
        settings: gmail-ingestor settings, for the output and database paths.
        label_name: Label whose backfilled articles should be removed.
        newsletters_dir: Root of the organised tree ingestor-tools writes.
        dry_run: Report what would be removed without touching anything.

    Returns:
        A PruneResult whose ``targets`` list is populated in both modes, so a
        dry run and a real run print the same table.
    """
    targets = plan_prune(settings, label_name, newsletters_dir=newsletters_dir)
    if not targets:
        return PruneResult(label=label_name, dry_run=dry_run)

    if dry_run:
        return PruneResult(
            label=label_name,
            targets=tuple(targets),
            files_removed=sum(len(t.files) for t in targets),
            directories_removed=sum(len(t.directories) for t in targets),
            rows_removed=len(targets),
            dry_run=True,
        )

    files_removed = directories_removed = 0

    # Files before rows: a crash between the two leaves rows pointing at
    # missing files, which the next scan simply rediscovers. The reverse order
    # would strand files that nothing knows about any more.
    for target in targets:
        for path in target.files:
            try:
                path.unlink()
                files_removed += 1
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning("Could not remove %s: %s", path, e)

        for directory in target.directories:
            try:
                shutil.rmtree(directory)
                directories_removed += 1
            except OSError as e:
                logger.warning("Could not remove %s: %s", directory, e)

    db_path = BackfillTracker.beside(settings.database_path).path
    with BackfillTracker(db_path) as tracker:
        rows_removed = tracker.delete_articles([t.article_id for t in targets])

    logger.info(
        "Pruned %s: %d files, %d directories, %d database rows",
        label_name, files_removed, directories_removed, rows_removed,
    )

    return PruneResult(
        label=label_name,
        targets=tuple(targets),
        files_removed=files_removed,
        directories_removed=directories_removed,
        rows_removed=rows_removed,
        dry_run=False,
    )
