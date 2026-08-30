"""Tests for the Backfill tab — mapping table, selection, and scan results."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, DataTable, Input, Static

from ingestor_tui.app import IngestorApp
from ingestor_tui.backfill.mappings import MappingStore
from ingestor_tui.backfill.models import ArticleRef, ScanEntry
from ingestor_tui.widgets.backfill import BackfillWidget

MAPPING_ENTRY = {
    "label_id": "Label_1",
    "archive_url": "https://x.test/archive",
    "sender": "Author <a@x.test>",
    "listing": {
        "mode": "json",
        "url_template": "https://x.test/api?offset={offset}&limit={limit}",
        "pagination": {"type": "offset"},
        "fields": {"url": "canonical_url", "title": "title"},
    },
}


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text("GMAIL_DATABASE_PATH=data/gmail_ingestor.db\n")
    return tmp_path


@pytest.fixture
def mappings(tmp_path: Path) -> MappingStore:
    store = MappingStore(tmp_path / "m.json")
    store.save("Example", MAPPING_ENTRY)
    store.save("Another", dict(MAPPING_ENTRY, archive_url="https://y.test/archive"))
    return store


def entry(title: str, missing: bool = True, reason: str = "") -> ScanEntry:
    return ScanEntry(
        ref=ArticleRef(
            url=f"https://x.test/p/{title.lower().replace(' ', '-')}",
            title=title,
            published_at=datetime(2026, 5, 1, tzinfo=UTC),
        ),
        article_id=f"web-{abs(hash(title)):016x}"[:20],
        status="discovered" if missing else "have",
        match_reason=reason,
    )


# --- app integration ---


@pytest.mark.asyncio
async def test_backfill_tab_exists(project_dir: Path) -> None:
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)):
        assert app.query_one("#backfill", BackfillWidget) is not None


@pytest.mark.asyncio
async def test_b_key_switches_to_backfill(project_dir: Path) -> None:
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("b")
        await pilot.pause()
        from textual.widgets import TabbedContent

        assert app.query_one("#tabs", TabbedContent).active == "tab-backfill"


@pytest.mark.asyncio
async def test_mappings_load_on_mount(project_dir: Path, mappings: MappingStore) -> None:
    app = IngestorApp(project_dir)
    app._mapping_store = mappings
    async with app.run_test(size=(120, 40)):
        app._load_mappings()
        assert app.query_one("#mappings-table", DataTable).row_count == 2


@pytest.mark.asyncio
async def test_malformed_mapping_file_does_not_crash(project_dir: Path, tmp_path: Path) -> None:
    """A bad mapping file must degrade to an empty tab, not take the app down."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    app = IngestorApp(project_dir)
    app._mapping_store = MappingStore(bad)
    async with app.run_test(size=(120, 40)):
        app._load_mappings()
        assert app.query_one("#mappings-table", DataTable).row_count == 0


# --- widget behaviour ---


@pytest.mark.asyncio
async def test_action_buttons_disabled_without_selection(
    project_dir: Path, mappings: MappingStore
) -> None:
    app = IngestorApp(project_dir)
    app._mapping_store = mappings
    async with app.run_test(size=(120, 40)):
        app._load_mappings()
        assert app.query_one("#btn-backfill-scan", Button).disabled
        assert app.query_one("#btn-backfill-run", Button).disabled


@pytest.mark.asyncio
async def test_selecting_a_mapping_enables_actions(
    project_dir: Path, mappings: MappingStore
) -> None:
    app = IngestorApp(project_dir)
    app._mapping_store = mappings
    async with app.run_test(size=(120, 40)) as pilot:
        app._load_mappings()
        widget = app.query_one("#backfill", BackfillWidget)
        table = app.query_one("#mappings-table", DataTable)

        key = table.coordinate_to_cell_key((0, 0)).row_key
        table.post_message(DataTable.RowSelected(table, 0, key))
        await pilot.pause()

        assert widget.selected_label == "Another"  # sorted first
        assert not app.query_one("#btn-backfill-scan", Button).disabled
        assert table.get_cell_at((0, 0)) == "✓"


@pytest.mark.asyncio
async def test_reselecting_clears_the_selection(
    project_dir: Path, mappings: MappingStore
) -> None:
    app = IngestorApp(project_dir)
    app._mapping_store = mappings
    async with app.run_test(size=(120, 40)) as pilot:
        app._load_mappings()
        widget = app.query_one("#backfill", BackfillWidget)
        table = app.query_one("#mappings-table", DataTable)
        key = table.coordinate_to_cell_key((0, 0)).row_key

        for _ in range(2):
            table.post_message(DataTable.RowSelected(table, 0, key))
            await pilot.pause()

        assert widget.selected_label is None
        assert app.query_one("#btn-backfill-run", Button).disabled


@pytest.mark.asyncio
async def test_reload_drops_a_selection_that_vanished(
    project_dir: Path, mappings: MappingStore
) -> None:
    """Scan must never dispatch against a label whose mapping was deleted."""
    app = IngestorApp(project_dir)
    app._mapping_store = mappings
    async with app.run_test(size=(120, 40)):
        widget = app.query_one("#backfill", BackfillWidget)
        widget._selected = "Example"
        mappings.delete("Example")
        app._load_mappings()

        assert widget.selected_label is None


@pytest.mark.asyncio
async def test_scan_results_render_missing_first(project_dir: Path) -> None:
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)):
        widget = app.query_one("#backfill", BackfillWidget)
        widget.populate_scan(
            [entry("Held One", missing=False, reason="exact"), entry("Missing One")]
        )

        table = app.query_one("#scan-table", DataTable)
        assert table.row_count == 2
        assert table.get_cell_at((0, 0)) == "MISSING"
        assert table.get_cell_at((1, 0)) == "have"


@pytest.mark.asyncio
async def test_scan_summary_counts(project_dir: Path) -> None:
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)):
        widget = app.query_one("#backfill", BackfillWidget)
        widget.populate_scan([entry("A"), entry("B"), entry("C", missing=False)])

        summary = app.query_one("#backfill-summary", Static)
        text = str(summary._Static__content)
        assert "3 listed" in text and "1 already held" in text and "2 missing" in text


@pytest.mark.asyncio
async def test_update_entry_status_ignores_unknown_rows(project_dir: Path) -> None:
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)):
        widget = app.query_one("#backfill", BackfillWidget)
        widget.populate_scan([entry("A")])
        widget.update_entry_status("web-does-not-exist", "done")  # must not raise


@pytest.mark.asyncio
async def test_get_params_reads_inputs(project_dir: Path, mappings: MappingStore) -> None:
    app = IngestorApp(project_dir)
    app._mapping_store = mappings
    async with app.run_test(size=(120, 40)):
        app._load_mappings()
        widget = app.query_one("#backfill", BackfillWidget)
        widget._selected = "Example"
        app.query_one("#input-backfill-limit", Input).value = "5"
        app.query_one("#cb-backfill-dry-run", Checkbox).value = True

        assert widget.get_params() == {"label_name": "Example", "limit": 5, "dry_run": True}


@pytest.mark.asyncio
async def test_get_params_empty_limit_is_none(project_dir: Path) -> None:
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)):
        assert app.query_one("#backfill", BackfillWidget).get_params()["limit"] is None


@pytest.mark.asyncio
async def test_set_running_toggles_stop(project_dir: Path, mappings: MappingStore) -> None:
    app = IngestorApp(project_dir)
    app._mapping_store = mappings
    async with app.run_test(size=(120, 40)):
        app._load_mappings()
        widget = app.query_one("#backfill", BackfillWidget)
        widget._selected = "Example"
        widget._update_selection_ui()

        widget.set_running(True)
        assert not app.query_one("#btn-backfill-stop", Button).disabled
        assert app.query_one("#btn-backfill-scan", Button).disabled

        widget.set_running(False)
        assert app.query_one("#btn-backfill-stop", Button).disabled
        assert not app.query_one("#btn-backfill-scan", Button).disabled


@pytest.mark.asyncio
async def test_progress_display(project_dir: Path) -> None:
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)):
        widget = app.query_one("#backfill", BackfillWidget)
        widget.update_progress("Fetching", 3, 10)
        assert "3/10" in str(app.query_one("#backfill-stage", Static)._Static__content)

        widget.reset_progress()
        assert "Idle" in str(app.query_one("#backfill-stage", Static)._Static__content)


@pytest.mark.asyncio
async def test_log_panel_captures_backfill_logs(project_dir: Path) -> None:
    """Backfill logs under ingestor_tui, so that logger must be piped in too."""
    import logging

    from ingestor_tui.widgets.log_panel import LogPanelWidget

    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)):
        panel = app.query_one("#log-panel", LogPanelWidget)
        assert "ingestor_tui" in panel.CAPTURED_LOGGERS
        assert "gmail_ingestor" in panel.CAPTURED_LOGGERS
        assert panel._handler in logging.getLogger("ingestor_tui").handlers
