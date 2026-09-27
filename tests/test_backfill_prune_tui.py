"""Tests for Prune in the Backfill tab — preview, confirm, then delete.

These drive the real ``prune_label`` against a temporary tree laid out like
email-analyzer itself, with the project directory beside ``output/`` and
``newsletters/``:

    <tmp>/gmail-ingestor/   the app chdirs here; .env and data/ live here
    <tmp>/output/           ../output/markdown and ../output/raw
    <tmp>/newsletters/      ../newsletters, prune's default

Mirroring the layout matters: prune's defaults are *relative* paths, and a
test project at ``<tmp>`` itself would resolve ``../newsletters`` to a
directory outside the test's own tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, DataTable, Static

from ingestor_tui.app import IngestorApp
from ingestor_tui.backfill.mappings import MappingStore
from ingestor_tui.backfill.store import BackfillTracker
from ingestor_tui.widgets.backfill import BackfillWidget
from ingestor_tui.widgets.confirm_dialog import ConfirmDialog

LABEL = "Example"
DONE_ID = "web-1111111111111111"
FAILED_ID = "web-2222222222222222"
HAVE_ID = "web-3333333333333333"
OTHER_ID = "web-4444444444444444"
GMAIL_ID = "19abcdef01234567"

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
def tree(tmp_path: Path) -> Path:
    """One backfilled article on disk in all three places, plus bystanders."""
    project = tmp_path / "gmail-ingestor"
    (project / "data").mkdir(parents=True)
    (project / ".env").write_text(
        "GMAIL_DATABASE_PATH=data/gmail_ingestor.db\n"
        "GMAIL_OUTPUT_MARKDOWN_DIR=../output/markdown\n"
        "GMAIL_OUTPUT_RAW_DIR=../output/raw\n"
    )

    markdown = tmp_path / "output" / "markdown"
    raw = tmp_path / "output" / "raw"
    article_dir = tmp_path / "newsletters" / LABEL / DONE_ID
    for directory in (markdown, raw, article_dir):
        directory.mkdir(parents=True)

    (markdown / f"a-real-post_{DONE_ID}.md").write_text("---\nid: x\n---\nbody\n")
    (raw / f"{DONE_ID}.html").write_text("<html></html>")
    (article_dir / f"{DONE_ID}.html").write_text("<html></html>")

    # A Gmail-ingested article in the same label: must survive untouched.
    (markdown / f"an-email_{GMAIL_ID}.md").write_text("---\nid: y\n---\n")
    (raw / f"{GMAIL_ID}.html").write_text("<html></html>")

    with BackfillTracker.beside(project / "data" / "gmail_ingestor.db") as tracker:
        for article_id, label, status in (
            (DONE_ID, LABEL, "done"),
            (FAILED_ID, LABEL, "failed"),
            (HAVE_ID, LABEL, "have"),
            (OTHER_ID, "Other", "done"),
        ):
            tracker.record_article(
                article_id=article_id,
                label_name=label,
                label_id="Label_1",
                url=f"https://x.test/p/{article_id}",
                title=f"Post {status}",
                published_at="2026-04-05T00:00:00",
                status=status,
            )
    return tmp_path


@pytest.fixture
def mappings(tmp_path: Path) -> MappingStore:
    store = MappingStore(tmp_path / "m.json")
    store.save(LABEL, MAPPING_ENTRY)
    store.save("Empty", dict(MAPPING_ENTRY, archive_url="https://y.test/archive"))
    return store


def _app(tree: Path, mappings: MappingStore) -> IngestorApp:
    app = IngestorApp(tree / "gmail-ingestor")
    app._mapping_store = mappings
    return app


async def _press_prune(app: IngestorApp, pilot, label: str = LABEL) -> None:
    """Select ``label`` on the Backfill tab, press Prune, wait for the plan."""
    app.action_switch_tab("tab-backfill")
    app._load_mappings()
    widget = app.query_one("#backfill", BackfillWidget)
    widget._selected = label
    widget._update_selection_ui()
    await pilot.pause()

    app.query_one("#btn-backfill-prune", Button).press()
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def _answer(app: IngestorApp, pilot, *, confirm: bool) -> None:
    """Answer the open confirmation dialog and wait for any resulting work."""
    assert isinstance(app.screen, ConfirmDialog)
    button = "#btn-confirm-yes" if confirm else "#btn-confirm-cancel"
    app.screen.query_one(button, Button).press()
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def _rows(app: IngestorApp) -> dict[str, str]:
    """Scan-table rows as {article_id: status}."""
    table = app.query_one("#scan-table", DataTable)
    return {str(row_key.value): table.get_cell(row_key, "col-status") for row_key in table.rows}


def _summary(app: IngestorApp) -> str:
    return str(app.query_one("#backfill-summary", Static)._Static__content)


def _paths(tree: Path) -> dict[str, Path]:
    return {
        "markdown": tree / "output" / "markdown" / f"a-real-post_{DONE_ID}.md",
        "raw": tree / "output" / "raw" / f"{DONE_ID}.html",
        "newsletters": tree / "newsletters" / LABEL / DONE_ID,
        "gmail_md": tree / "output" / "markdown" / f"an-email_{GMAIL_ID}.md",
        "gmail_raw": tree / "output" / "raw" / f"{GMAIL_ID}.html",
    }


def _db_ids(tree: Path, label: str) -> set[str]:
    db = tree / "gmail-ingestor" / "data" / "gmail_ingestor.db"
    with BackfillTracker.beside(db) as tracker:
        return {row["article_id"] for row in tracker.articles_for_label(label)}


# --- the button ---


@pytest.mark.asyncio
async def test_prune_button_follows_selection_and_running_state(
    tree: Path, mappings: MappingStore
) -> None:
    app = _app(tree, mappings)
    async with app.run_test(size=(140, 45)):
        app._load_mappings()
        widget = app.query_one("#backfill", BackfillWidget)
        prune = app.query_one("#btn-backfill-prune", Button)
        assert prune.disabled, "no mapping selected yet"

        widget._selected = LABEL
        widget._update_selection_ui()
        assert not prune.disabled

        widget.set_running(True)
        assert prune.disabled, "must not prune mid-run"
        widget.set_running(False)
        assert not prune.disabled


@pytest.mark.asyncio
async def test_non_cancellable_runs_keep_stop_disabled(tree: Path, mappings: MappingStore) -> None:
    """prune_label has no stop hook; an enabled Stop would promise nothing."""
    app = _app(tree, mappings)
    async with app.run_test(size=(140, 45)):
        widget = app.query_one("#backfill", BackfillWidget)
        widget.set_running(True, cancellable=False)
        assert app.query_one("#btn-backfill-stop", Button).disabled
        assert app.query_one("#btn-backfill-reload", Button).disabled


# --- preview, then ask ---


@pytest.mark.asyncio
async def test_prune_previews_and_asks_before_deleting(tree: Path, mappings: MappingStore) -> None:
    app = _app(tree, mappings)
    async with app.run_test(size=(140, 45)) as pilot:
        await _press_prune(app, pilot)

        # `have` rows point at no backfilled file and are not prune targets.
        assert _rows(app) == {DONE_ID: "prune", FAILED_ID: "prune"}
        assert "would remove 2 file(s), 1 newsletter folder(s), 2 database row(s)" in _summary(app)

        assert isinstance(app.screen, ConfirmDialog)
        assert all(path.exists() for path in _paths(tree).values()), "preview deleted something"


@pytest.mark.asyncio
async def test_confirming_prune_deletes_every_copy(tree: Path, mappings: MappingStore) -> None:
    app = _app(tree, mappings)
    async with app.run_test(size=(140, 45)) as pilot:
        await _press_prune(app, pilot)
        await _answer(app, pilot, confirm=True)

        paths = _paths(tree)
        assert not paths["markdown"].exists()
        assert not paths["raw"].exists()
        assert not paths["newsletters"].exists()
        assert _db_ids(tree, LABEL) == {HAVE_ID}

        assert _rows(app) == {DONE_ID: "removed", FAILED_ID: "removed"}
        assert "removed 2 file(s)" in _summary(app)
        # Controls are released once the run has finished.
        assert not app.query_one("#btn-backfill-prune", Button).disabled


@pytest.mark.asyncio
async def test_confirming_prune_leaves_bystanders_alone(tree: Path, mappings: MappingStore) -> None:
    """Gmail-ingested files and other labels' rows are never prune targets."""
    app = _app(tree, mappings)
    async with app.run_test(size=(140, 45)) as pilot:
        await _press_prune(app, pilot)
        await _answer(app, pilot, confirm=True)

        paths = _paths(tree)
        assert paths["gmail_md"].exists()
        assert paths["gmail_raw"].exists()
        assert _db_ids(tree, "Other") == {OTHER_ID}


@pytest.mark.asyncio
async def test_confirming_prune_refreshes_the_mapping_state(
    tree: Path, mappings: MappingStore
) -> None:
    app = _app(tree, mappings)
    async with app.run_test(size=(140, 45)) as pilot:
        table = app.query_one("#mappings-table", DataTable)
        await _press_prune(app, pilot)
        assert "done=1" in table.get_cell(LABEL, "col-state")

        await _answer(app, pilot, confirm=True)
        state = table.get_cell(LABEL, "col-state")
        assert "done" not in state
        assert "have=1" in state


@pytest.mark.asyncio
async def test_cancelling_prune_deletes_nothing(tree: Path, mappings: MappingStore) -> None:
    app = _app(tree, mappings)
    async with app.run_test(size=(140, 45)) as pilot:
        await _press_prune(app, pilot)
        await _answer(app, pilot, confirm=False)

        assert all(path.exists() for path in _paths(tree).values())
        assert _db_ids(tree, LABEL) == {DONE_ID, FAILED_ID, HAVE_ID}
        assert not app.query_one("#btn-backfill-prune", Button).disabled


@pytest.mark.asyncio
async def test_dry_run_previews_without_asking(tree: Path, mappings: MappingStore) -> None:
    """Mirrors Backfill: a dry run changes nothing, so there is nothing to confirm."""
    app = _app(tree, mappings)
    async with app.run_test(size=(140, 45)) as pilot:
        app.query_one("#cb-backfill-dry-run", Checkbox).value = True
        await _press_prune(app, pilot)

        assert not isinstance(app.screen, ConfirmDialog)
        assert _rows(app) == {DONE_ID: "prune", FAILED_ID: "prune"}
        assert all(path.exists() for path in _paths(tree).values())
        assert not app.query_one("#btn-backfill-prune", Button).disabled


@pytest.mark.asyncio
async def test_nothing_to_prune_does_not_ask(tree: Path, mappings: MappingStore) -> None:
    app = _app(tree, mappings)
    async with app.run_test(size=(140, 45)) as pilot:
        await _press_prune(app, pilot, label="Empty")

        assert not isinstance(app.screen, ConfirmDialog)
        assert _rows(app) == {}
        assert "nothing to prune" in _summary(app)
        assert not app.query_one("#btn-backfill-prune", Button).disabled


# --- the confirmation dialog ---


@pytest.mark.asyncio
async def test_prune_dialog_is_destructive_and_focuses_cancel(
    tree: Path, mappings: MappingStore
) -> None:
    """A reflexive Enter on a delete prompt must back out, not delete."""
    app = _app(tree, mappings)
    async with app.run_test(size=(140, 45)) as pilot:
        await _press_prune(app, pilot)

        dialog = app.screen
        assert isinstance(dialog, ConfirmDialog)
        assert dialog.has_class("destructive")
        assert dialog.query_one("#btn-confirm-yes", Button).variant == "error"
        assert str(dialog.query_one("#btn-confirm-yes", Button).label) == "Prune"
        assert app.focused is dialog.query_one("#btn-confirm-cancel", Button)

        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert all(path.exists() for path in _paths(tree).values())


@pytest.mark.asyncio
async def test_default_confirm_dialog_is_unchanged(tree: Path, mappings: MappingStore) -> None:
    """Existing callers (the Gmail operations, Backfill) keep the old dialog."""
    app = _app(tree, mappings)
    async with app.run_test(size=(140, 45)) as pilot:
        app.push_screen(ConfirmDialog("Run it?"))
        await pilot.pause()

        dialog = app.screen
        assert not dialog.has_class("destructive")
        yes = dialog.query_one("#btn-confirm-yes", Button)
        assert str(yes.label) == "Yes"
        assert yes.variant == "primary"
