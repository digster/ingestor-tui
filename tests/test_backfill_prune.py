"""Tests for prune — removing what a backfill run wrote, everywhere it wrote it.

The reason this command exists is asserted here too: an article lives in four
places, and ingestor-tools' organizer only ever *copies*, skipping files already
present. So a prune that cleans ../output but leaves ../newsletters alone would
be worse than useless — the stale copy is what gets published.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from gmail_ingestor.config.settings import GmailIngestorSettings

from ingestor_tui.backfill.prune import plan_prune, prune_label
from ingestor_tui.backfill.store import BackfillTracker

LABEL = "Example Newsletter"
DONE_ID = "web-1111111111111111"
FAILED_ID = "web-2222222222222222"
HAVE_ID = "web-3333333333333333"


@pytest.fixture
def project(tmp_path: Path) -> GmailIngestorSettings:
    """An email-analyzer-shaped tree with one backfilled article on disk."""
    markdown = tmp_path / "output" / "markdown"
    raw = tmp_path / "output" / "raw"
    label_dir = tmp_path / "newsletters" / LABEL / DONE_ID
    for directory in (markdown, raw, label_dir, tmp_path / "data"):
        directory.mkdir(parents=True)

    (markdown / f"a-real-post_{DONE_ID}.md").write_text("---\nid: x\n---\nbody\n")
    (raw / f"{DONE_ID}.html").write_text("<html></html>")
    (label_dir / f"a-real-post_{DONE_ID}.md").write_text("---\nid: x\n---\nbody\n")
    (label_dir / f"{DONE_ID}.html").write_text("<html></html>")

    # An unrelated Gmail-ingested article that must survive untouched.
    (markdown / "an-email_19abcdef01234567.md").write_text("---\nid: y\n---\n")
    (raw / "19abcdef01234567.html").write_text("<html></html>")

    settings = GmailIngestorSettings(
        output_markdown_dir=markdown,
        output_raw_dir=raw,
        database_path=tmp_path / "data" / "gmail_ingestor.db",
    )

    with BackfillTracker.beside(settings.database_path) as tracker:
        for article_id, status in (
            (DONE_ID, "done"),
            (FAILED_ID, "failed"),
            (HAVE_ID, "have"),
        ):
            tracker.record_article(
                article_id=article_id,
                label_name=LABEL,
                label_id="Label_1",
                url=f"https://x.test/p/{article_id}",
                title=f"Post {status}",
                published_at="2026-04-05T00:00:00",
                status=status,
            )
        tracker.record_article(
            article_id="web-4444444444444444",
            label_name="Other Newsletter",
            label_id="Label_2",
            url="https://y.test/p/z",
            title="Someone else's",
            status="done",
        )

    return settings


def _prune(settings: GmailIngestorSettings, **kwargs):
    newsletters = Path(settings.output_markdown_dir).parent.parent / "newsletters"
    return prune_label(settings, LABEL, newsletters_dir=newsletters, **kwargs)


# --- planning ---


def test_plan_skips_have_rows(project: GmailIngestorSettings) -> None:
    """A `have` row points at a Gmail message, not at anything backfill wrote."""
    newsletters = Path(project.output_markdown_dir).parent.parent / "newsletters"
    ids = {t.article_id for t in plan_prune(project, LABEL, newsletters_dir=newsletters)}
    assert ids == {DONE_ID, FAILED_ID}


def test_plan_is_scoped_to_one_label(project: GmailIngestorSettings) -> None:
    newsletters = Path(project.output_markdown_dir).parent.parent / "newsletters"
    targets = plan_prune(project, "Other Newsletter", newsletters_dir=newsletters)
    assert [t.article_id for t in targets] == ["web-4444444444444444"]


def test_plan_reports_which_targets_have_files(project: GmailIngestorSettings) -> None:
    newsletters = Path(project.output_markdown_dir).parent.parent / "newsletters"
    by_id = {
        t.article_id: t for t in plan_prune(project, LABEL, newsletters_dir=newsletters)
    }
    assert by_id[DONE_ID].is_present
    assert not by_id[FAILED_ID].is_present, "a failed article wrote nothing"


# --- dry run ---


def test_dry_run_deletes_nothing(project: GmailIngestorSettings) -> None:
    result = _prune(project, dry_run=True)

    assert result.dry_run
    assert result.files_removed == 2
    assert (Path(project.output_raw_dir) / f"{DONE_ID}.html").exists()
    with BackfillTracker.beside(project.database_path) as tracker:
        assert tracker.get_article(DONE_ID) is not None


# --- applying ---


def test_prune_clears_all_three_locations(project: GmailIngestorSettings) -> None:
    root = Path(project.output_markdown_dir).parent.parent
    result = _prune(project, dry_run=False)

    assert not result.dry_run
    assert result.files_removed == 2
    assert result.directories_removed == 1
    assert result.rows_removed == 2

    assert not list(Path(project.output_markdown_dir).glob(f"*_{DONE_ID}.md"))
    assert not (Path(project.output_raw_dir) / f"{DONE_ID}.html").exists()
    assert not (root / "newsletters" / LABEL / DONE_ID).exists()


def test_prune_forgets_the_rows_so_a_scan_rediscovers_them(
    project: GmailIngestorSettings,
) -> None:
    """Leaving a `done` row behind would make the article permanently
    invisible: gone from disk, and skipped by completed_urls forever."""
    _prune(project, dry_run=False)

    with BackfillTracker.beside(project.database_path) as tracker:
        assert tracker.get_article(DONE_ID) is None
        assert tracker.completed_urls(LABEL) == set()
        assert tracker.get_article(HAVE_ID) is not None, "have rows are untouched"


def test_prune_leaves_ingested_articles_alone(project: GmailIngestorSettings) -> None:
    _prune(project, dry_run=False)

    assert (Path(project.output_markdown_dir) / "an-email_19abcdef01234567.md").exists()
    assert (Path(project.output_raw_dir) / "19abcdef01234567.html").exists()


def test_prune_leaves_other_labels_alone(project: GmailIngestorSettings) -> None:
    _prune(project, dry_run=False)

    with BackfillTracker.beside(project.database_path) as tracker:
        assert tracker.get_article("web-4444444444444444") is not None


def test_prune_is_idempotent(project: GmailIngestorSettings) -> None:
    _prune(project, dry_run=False)
    second = _prune(project, dry_run=False)

    assert second.targets == ()
    assert second.files_removed == 0


def test_prune_with_no_database_is_a_no_op(tmp_path: Path) -> None:
    settings = GmailIngestorSettings(
        output_markdown_dir=tmp_path / "md",
        output_raw_dir=tmp_path / "raw",
        database_path=tmp_path / "data" / "gmail_ingestor.db",
    )
    result = prune_label(settings, LABEL, newsletters_dir=tmp_path / "newsletters")
    assert result.targets == ()


def test_prune_reclaims_files_orphaned_by_a_downgraded_row(
    project: GmailIngestorSettings,
) -> None:
    """An older build let a re-scan overwrite `done` with `have`, stranding the
    files it had written. Status decides usually; files on disk always win."""
    raw = Path(project.output_raw_dir)
    markdown = Path(project.output_markdown_dir)
    (markdown / f"orphan_{HAVE_ID}.md").write_text("---\nid: z\n---\n")
    (raw / f"{HAVE_ID}.html").write_text("<html></html>")

    result = _prune(project, dry_run=False)

    assert HAVE_ID in {t.article_id for t in result.targets}
    assert not (raw / f"{HAVE_ID}.html").exists()


def test_a_have_row_with_no_files_is_still_left_alone(
    project: GmailIngestorSettings,
) -> None:
    result = _prune(project, dry_run=False)
    assert HAVE_ID not in {t.article_id for t in result.targets}
