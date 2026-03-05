"""Smoke tests for IngestorApp — verifies app mounts and tabs render."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestor_tui.app import IngestorApp
from ingestor_tui.preset_store import PresetStore
from ingestor_tui.widgets.labels import LabelsSelected, LabelsWidget
from textual.widgets import Checkbox

from ingestor_tui.widgets.operations import OperationsWidget


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal project directory with a .env file."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GMAIL_LABEL=INBOX\n"
        "GMAIL_DATABASE_PATH=data/gmail_ingestor.db\n"
    )
    return tmp_path


@pytest.mark.asyncio
async def test_app_mounts(project_dir: Path) -> None:
    """App should mount without errors."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.title == "Gmail Ingestor TUI"


@pytest.mark.asyncio
async def test_tabs_exist(project_dir: Path) -> None:
    """All four tabs should be present."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        tabs = app.query("TabPane")
        tab_ids = {t.id for t in tabs}
        assert "tab-dashboard" in tab_ids
        assert "tab-operations" in tab_ids
        assert "tab-labels" in tab_ids
        assert "tab-log" in tab_ids


@pytest.mark.asyncio
async def test_tab_order(project_dir: Path) -> None:
    """Tabs should be ordered: Dashboard, Labels, Operations, Log."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        tabs = list(app.query("TabPane"))
        tab_ids = [t.id for t in tabs]
        assert tab_ids == [
            "tab-dashboard",
            "tab-labels",
            "tab-operations",
            "tab-log",
        ]


@pytest.mark.asyncio
async def test_dashboard_widget_present(project_dir: Path) -> None:
    """Dashboard widget should be mounted."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        dashboard = app.query_one("#dashboard")
        assert dashboard is not None


@pytest.mark.asyncio
async def test_operations_buttons_present(project_dir: Path) -> None:
    """Operations tab should have all pipeline buttons including Stop."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        button_ids = {b.id for b in app.query("Button") if b.id and b.id.startswith("btn-")}
        expected = {
            "btn-full-fetch",
            "btn-discover",
            "btn-fetch-pending",
            "btn-convert-pending",
            "btn-retry-failed",
            "btn-stop",
        }
        assert expected.issubset(button_ids)


@pytest.mark.asyncio
async def test_stop_button_disabled_by_default(project_dir: Path) -> None:
    """Stop button should be disabled when no operation is running."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        stop_btn = app.query_one("#btn-stop")
        assert stop_btn.disabled is True


@pytest.mark.asyncio
async def test_labels_filter_input_present(project_dir: Path) -> None:
    """Labels tab should have a filter input field."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        filter_input = app.query_one("#labels-filter")
        assert filter_input is not None


@pytest.mark.asyncio
async def test_project_dir_input_present(project_dir: Path) -> None:
    """Dashboard should have a project directory input."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        project_input = app.query_one("#input-project-dir")
        assert project_input is not None
        assert project_input.value == str(project_dir)


@pytest.mark.asyncio
async def test_apply_project_dir_button_present(project_dir: Path) -> None:
    """Dashboard should have an Apply button for project directory."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        apply_btn = app.query_one("#btn-apply-project-dir")
        assert apply_btn is not None


@pytest.mark.asyncio
async def test_parse_labels_single() -> None:
    """_parse_labels should return a single label."""
    assert IngestorApp._parse_labels("INBOX") == ["INBOX"]


@pytest.mark.asyncio
async def test_parse_labels_multiple() -> None:
    """_parse_labels should split comma-separated labels."""
    assert IngestorApp._parse_labels("INBOX, SENT, TRASH") == ["INBOX", "SENT", "TRASH"]


@pytest.mark.asyncio
async def test_parse_labels_empty() -> None:
    """_parse_labels with None/empty should return [None]."""
    assert IngestorApp._parse_labels(None) == [None]
    assert IngestorApp._parse_labels("") == [None]


# --- Label selection tests ---


@pytest.mark.asyncio
async def test_labels_copy_clear_buttons_exist(project_dir: Path) -> None:
    """Labels tab should have Copy to Operations and Clear Selection buttons."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        copy_btn = app.query_one("#btn-copy-labels")
        clear_btn = app.query_one("#btn-clear-selection")
        assert copy_btn is not None
        assert clear_btn is not None


@pytest.mark.asyncio
async def test_labels_buttons_disabled_by_default(project_dir: Path) -> None:
    """Copy and Clear buttons should be disabled when no labels are selected."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        copy_btn = app.query_one("#btn-copy-labels")
        clear_btn = app.query_one("#btn-clear-selection")
        assert copy_btn.disabled is True
        assert clear_btn.disabled is True


@pytest.mark.asyncio
async def test_labels_selection_count_starts_at_zero(project_dir: Path) -> None:
    """Selection count indicator should show '0 selected' initially."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        labels_widget = app.query_one("#labels", LabelsWidget)
        assert len(labels_widget._selected_ids) == 0


@pytest.mark.asyncio
async def test_labels_selected_updates_operations_input(project_dir: Path) -> None:
    """LabelsSelected message should populate the Operations label input and switch tab."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        labels_widget = app.query_one("#labels", LabelsWidget)
        labels_widget.post_message(LabelsSelected(["INBOX", "SENT"], "INBOX, SENT"))
        await pilot.pause()

        input_label = app.query_one("#input-label")
        assert input_label.value == "INBOX, SENT"


@pytest.mark.asyncio
async def test_labels_single_selection(project_dir: Path) -> None:
    """Clicking a label row should toggle its selection and update the count."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        labels_widget = app.query_one("#labels", LabelsWidget)
        labels_widget.populate([
            {"id": "INBOX", "name": "Inbox"},
            {"id": "SENT", "name": "Sent"},
        ])
        await pilot.pause()

        assert "INBOX" not in labels_widget._selected_ids

        # Simulate row selection
        from textual.widgets import DataTable
        table = labels_widget.query_one("#labels-table", DataTable)
        table.move_cursor(row=0)
        table.action_select_cursor()
        await pilot.pause()

        assert "INBOX" in labels_widget._selected_ids
        assert len(labels_widget._selected_ids) == 1


# --- Preset UI tests ---


@pytest.mark.asyncio
async def test_preset_widgets_exist(project_dir: Path) -> None:
    """Operations tab should have preset Select and buttons."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#select-preset") is not None
        assert app.query_one("#btn-preset-load") is not None
        assert app.query_one("#btn-preset-save") is not None
        assert app.query_one("#btn-preset-del") is not None


@pytest.mark.asyncio
async def test_preset_load_populates_input(project_dir: Path, tmp_path: Path) -> None:
    """Loading a preset should populate the label input."""
    store = PresetStore(path=tmp_path / "presets.json")
    store.save_preset("work", "INBOX, SENT")

    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        ops = app.query_one("#operations", OperationsWidget)
        ops._preset_store = store
        ops._refresh_presets()
        await pilot.pause()

        from textual.widgets import Select
        select = ops.query_one("#select-preset", Select)
        select.value = "work"
        await pilot.pause()

        ops._handle_load()
        await pilot.pause()

        from textual.widgets import Input
        assert ops.query_one("#input-label", Input).value == "INBOX, SENT"


@pytest.mark.asyncio
async def test_preset_save_empty_warns(project_dir: Path) -> None:
    """Saving with empty label input should show a warning notification."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40), notifications=True) as pilot:
        ops = app.query_one("#operations", OperationsWidget)

        from textual.widgets import Input
        ops.query_one("#input-label", Input).value = ""
        ops._handle_save()
        await pilot.pause()

        # Check that a warning notification was posted
        assert any("empty" in str(n.message).lower() for n in app._notifications)


@pytest.mark.asyncio
async def test_preset_delete_no_selection_warns(project_dir: Path) -> None:
    """Deleting with no preset selected should show a warning."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40), notifications=True) as pilot:
        await pilot.pause()

        ops = app.query_one("#operations", OperationsWidget)
        ops._handle_delete()
        await pilot.pause()

        assert any("select" in str(n.message).lower() for n in app._notifications)


# --- Full Sync checkbox tests ---


@pytest.mark.asyncio
async def test_full_sync_checkbox_present(project_dir: Path) -> None:
    """Operations tab should have a Full Sync checkbox."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        cb = app.query_one("#cb-full-sync", Checkbox)
        assert cb is not None
        assert cb.value is False


@pytest.mark.asyncio
async def test_get_params_includes_force_full_sync(project_dir: Path) -> None:
    """get_params() should include force_full_sync, defaulting to False."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        ops = app.query_one("#operations", OperationsWidget)
        params = ops.get_params()
        assert "force_full_sync" in params
        assert params["force_full_sync"] is False


@pytest.mark.asyncio
async def test_full_sync_checkbox_toggles_param(project_dir: Path) -> None:
    """Toggling the checkbox should change force_full_sync in get_params()."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        ops = app.query_one("#operations", OperationsWidget)
        cb = ops.query_one("#cb-full-sync", Checkbox)

        cb.value = True
        await pilot.pause()
        assert ops.get_params()["force_full_sync"] is True

        cb.value = False
        await pilot.pause()
        assert ops.get_params()["force_full_sync"] is False
