"""Tests for backfill.db state tracking."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestor_tui.backfill.store import BackfillTracker


@pytest.fixture
def tracker(tmp_path: Path):
    with BackfillTracker(tmp_path / "backfill.db") as t:
        yield t


def _record(tracker: BackfillTracker, article_id: str, url: str, **kwargs) -> None:
    tracker.record_article(
        article_id=article_id,
        label_name=kwargs.pop("label_name", "Example"),
        label_id=kwargs.pop("label_id", "Label_1"),
        url=url,
        title=kwargs.pop("title", "A Post"),
        **kwargs,
    )


def test_beside_places_db_next_to_the_gmail_one(tmp_path: Path) -> None:
    """Backfill state lives alongside gmail_ingestor.db, never inside it."""
    gmail_db = tmp_path / "data" / "gmail_ingestor.db"
    path = BackfillTracker.beside(gmail_db).path
    assert path == tmp_path / "data" / "backfill.db"
    assert path != gmail_db


def test_creates_schema_on_connect(tmp_path: Path) -> None:
    with BackfillTracker(tmp_path / "nested" / "backfill.db") as tracker:
        assert tracker.count_by_status() == {}
    assert (tmp_path / "nested" / "backfill.db").exists()


def test_conn_before_connect_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not connected"):
        _ = BackfillTracker(tmp_path / "b.db").conn


def test_record_and_read_back(tracker: BackfillTracker) -> None:
    _record(tracker, "web-a", "https://x.test/p/a", published_at="2026-05-01T00:00:00Z")
    row = tracker.get_article("web-a")
    assert row["url"] == "https://x.test/p/a"
    assert row["status"] == "discovered"
    assert row["label_name"] == "Example"


def test_record_is_idempotent(tracker: BackfillTracker) -> None:
    _record(tracker, "web-a", "https://x.test/p/a")
    _record(tracker, "web-a", "https://x.test/p/a", title="Retitled")
    assert tracker.count_by_status() == {"discovered": 1}
    assert tracker.get_article("web-a")["title"] == "Retitled"


def test_invalid_status_rejected(tracker: BackfillTracker) -> None:
    with pytest.raises(ValueError, match="Invalid status"):
        _record(tracker, "web-a", "https://x.test/p/a", status="nonsense")


def test_mark_done_records_paths(tracker: BackfillTracker) -> None:
    _record(tracker, "web-a", "https://x.test/p/a")
    tracker.mark_done("web-a", raw_html_path="/raw/web-a.html", markdown_path="/md/a.md")

    row = tracker.get_article("web-a")
    assert row["status"] == "done"
    assert row["markdown_path"] == "/md/a.md"
    assert row["error_message"] == ""


def test_mark_failed_keeps_the_error(tracker: BackfillTracker) -> None:
    _record(tracker, "web-a", "https://x.test/p/a")
    tracker.mark_failed("web-a", "HTTP 404")
    row = tracker.get_article("web-a")
    assert row["status"] == "failed"
    assert row["error_message"] == "HTTP 404"


def test_long_errors_are_truncated(tracker: BackfillTracker) -> None:
    _record(tracker, "web-a", "https://x.test/p/a")
    tracker.mark_failed("web-a", "x" * 5000)
    assert len(tracker.get_article("web-a")["error_message"]) == 1000


def test_completed_urls_only_returns_done(tracker: BackfillTracker) -> None:
    """Failed and discovered rows stay eligible for a retry on the next run."""
    _record(tracker, "web-a", "https://x.test/p/a")
    _record(tracker, "web-b", "https://x.test/p/b")
    _record(tracker, "web-c", "https://x.test/p/c")
    tracker.mark_done("web-a", raw_html_path="r", markdown_path="m")
    tracker.mark_failed("web-b", "boom")

    assert tracker.completed_urls("Example") == {"https://x.test/p/a"}


def test_completed_urls_are_scoped_by_label(tracker: BackfillTracker) -> None:
    _record(tracker, "web-a", "https://x.test/p/a", label_name="Alpha")
    _record(tracker, "web-b", "https://x.test/p/b", label_name="Beta")
    tracker.mark_done("web-a", raw_html_path="r", markdown_path="m")
    tracker.mark_done("web-b", raw_html_path="r", markdown_path="m")

    assert tracker.completed_urls("Alpha") == {"https://x.test/p/a"}


def test_count_by_status_scopes_to_label(tracker: BackfillTracker) -> None:
    _record(tracker, "web-a", "https://x.test/p/a", label_name="Alpha")
    _record(tracker, "web-b", "https://x.test/p/b", label_name="Beta")
    tracker.mark_done("web-a", raw_html_path="r", markdown_path="m")

    assert tracker.count_by_status("Alpha") == {"done": 1}
    assert tracker.count_by_status() == {"done": 1, "discovered": 1}


def test_get_missing_article_returns_none(tracker: BackfillTracker) -> None:
    assert tracker.get_article("web-nope") is None


def test_run_lifecycle(tracker: BackfillTracker) -> None:
    run_id = tracker.start_run("Example")
    assert tracker.last_run("Example")["completed_at"] is None

    tracker.complete_run(run_id, articles_listed=10, articles_held=7,
                         articles_written=3, articles_failed=0)
    run = tracker.last_run("Example")
    assert run["completed_at"] is not None
    assert (run["articles_listed"], run["articles_written"]) == (10, 3)


def test_last_run_returns_the_newest(tracker: BackfillTracker) -> None:
    tracker.complete_run(tracker.start_run("Example"), articles_written=1)
    tracker.complete_run(tracker.start_run("Example"), articles_written=9)
    assert tracker.last_run("Example")["articles_written"] == 9


def test_last_run_for_unknown_label(tracker: BackfillTracker) -> None:
    assert tracker.last_run("Nobody") is None


def test_recording_a_scan_does_not_downgrade_a_done_article(
    tracker: BackfillTracker,
) -> None:
    """`done` says files exist on disk; a re-scan must not erase that.

    classify() reports an already-backfilled URL as `have`, and the runner
    records every classified entry. Without this guard the second scan
    overwrote the row's status and paths, so the article's files became
    invisible to prune and it was re-fetched on the run after that.
    """
    _record(tracker, "web-1", "https://x.test/p/a")
    tracker.mark_done("web-1", raw_html_path="raw/web-1.html", markdown_path="md/a.md")

    _record(tracker, "web-1", "https://x.test/p/a", status="have",
            match_reason="already backfilled")

    row = tracker.get_article("web-1")
    assert row["status"] == "done"
    assert row["raw_html_path"] == "raw/web-1.html"
    assert row["markdown_path"] == "md/a.md"
    assert tracker.completed_urls("Example") == {"https://x.test/p/a"}


def test_recording_still_updates_metadata_on_a_done_article(
    tracker: BackfillTracker,
) -> None:
    """Only the status is pinned — a retitled post still updates."""
    _record(tracker, "web-1", "https://x.test/p/a", title="Old Headline")
    tracker.mark_done("web-1", raw_html_path="r", markdown_path="m")

    _record(tracker, "web-1", "https://x.test/p/a", title="New Headline", status="have")

    row = tracker.get_article("web-1")
    assert row["title"] == "New Headline"
    assert row["status"] == "done"


def test_non_done_statuses_still_transition_freely(tracker: BackfillTracker) -> None:
    _record(tracker, "web-1", "https://x.test/p/a", status="discovered")
    _record(tracker, "web-1", "https://x.test/p/a", status="have")
    assert tracker.get_article("web-1")["status"] == "have"


def test_articles_for_label_returns_every_status(tracker: BackfillTracker) -> None:
    _record(tracker, "web-1", "https://x.test/p/a", status="done")
    _record(tracker, "web-2", "https://x.test/p/b", status="have")
    _record(tracker, "web-3", "https://y.test/p/c", label_name="Other")

    ids = {r["article_id"] for r in tracker.articles_for_label("Example")}
    assert ids == {"web-1", "web-2"}


def test_delete_articles_forgets_only_what_it_is_given(tracker: BackfillTracker) -> None:
    _record(tracker, "web-1", "https://x.test/p/a")
    _record(tracker, "web-2", "https://x.test/p/b")

    assert tracker.delete_articles(["web-1"]) == 1
    assert tracker.get_article("web-1") is None
    assert tracker.get_article("web-2") is not None


def test_delete_articles_handles_an_empty_list(tracker: BackfillTracker) -> None:
    assert tracker.delete_articles([]) == 0


def test_delete_articles_exceeds_the_sqlite_variable_limit(
    tracker: BackfillTracker,
) -> None:
    """A whole-label prune can easily pass more IDs than SQLite allows in one
    statement, so the delete is chunked."""
    ids = [f"web-{i:016d}" for i in range(1200)]
    for article_id in ids:
        _record(tracker, article_id, f"https://x.test/p/{article_id}")

    assert tracker.delete_articles(ids) == 1200
    assert tracker.count_by_status("Example") == {}
