"""Smoke tests for IngestorApp — verifies app mounts and tabs render."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestor_tui.app import IngestorApp, _build_cli_command
from ingestor_tui.preset_store import PresetStore
from ingestor_tui.widgets.labels import LabelsSelected, LabelsWidget
from ingestor_tui.widgets.log_panel import LogPanelWidget
from textual.widgets import Checkbox, Static

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


# --- Preset load syncs Labels selection ---


@pytest.mark.asyncio
async def test_preset_load_syncs_labels_selection(project_dir: Path, tmp_path: Path) -> None:
    """Loading a preset should update the Labels widget's _selected_ids."""
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

        labels_widget = app.query_one("#labels", LabelsWidget)
        assert labels_widget._selected_ids == {"INBOX", "SENT"}


@pytest.mark.asyncio
async def test_preset_load_updates_datatable_checkmarks(project_dir: Path, tmp_path: Path) -> None:
    """Loading a preset should show checkmarks in the Labels DataTable."""
    store = PresetStore(path=tmp_path / "presets.json")
    store.save_preset("work", "INBOX, SENT")

    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        # Populate labels first
        labels_widget = app.query_one("#labels", LabelsWidget)
        labels_widget.populate([
            {"id": "INBOX", "name": "Inbox"},
            {"id": "SENT", "name": "Sent"},
            {"id": "TRASH", "name": "Trash"},
        ])
        await pilot.pause()

        # Load the preset
        ops = app.query_one("#operations", OperationsWidget)
        ops._preset_store = store
        ops._refresh_presets()
        await pilot.pause()

        from textual.widgets import DataTable, Select
        select = ops.query_one("#select-preset", Select)
        select.value = "work"
        await pilot.pause()

        ops._handle_load()
        await pilot.pause()

        # Verify checkmarks in table
        table = labels_widget.query_one("#labels-table", DataTable)
        inbox_check = table.get_cell("INBOX", "col-check")
        sent_check = table.get_cell("SENT", "col-check")
        trash_check = table.get_cell("TRASH", "col-check")
        assert inbox_check == "\u2713"
        assert sent_check == "\u2713"
        assert trash_check == ""


@pytest.mark.asyncio
async def test_preset_load_replaces_previous_selection(project_dir: Path, tmp_path: Path) -> None:
    """Loading a preset should replace any prior label selection, not merge."""
    store = PresetStore(path=tmp_path / "presets.json")
    store.save_preset("work", "SENT")

    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        # Pre-select INBOX manually
        labels_widget = app.query_one("#labels", LabelsWidget)
        labels_widget._selected_ids = {"INBOX", "TRASH"}

        # Load a preset with only SENT
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

        # Old selection should be gone, only preset labels remain
        assert labels_widget._selected_ids == {"SENT"}


@pytest.mark.asyncio
async def test_preset_load_updates_selection_count(project_dir: Path, tmp_path: Path) -> None:
    """Loading a preset should update the selection count badge and enable buttons."""
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

        labels_widget = app.query_one("#labels", LabelsWidget)
        count_label = labels_widget.query_one("#labels-selection-count", Static)
        assert "2 selected" in str(count_label._Static__content)

        # Copy and Clear buttons should be enabled
        copy_btn = labels_widget.query_one("#btn-copy-labels")
        clear_btn = labels_widget.query_one("#btn-clear-selection")
        assert copy_btn.disabled is False
        assert clear_btn.disabled is False


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


# --- Command label tests ---


@pytest.mark.asyncio
async def test_command_label_present(project_dir: Path) -> None:
    """Log panel should have a command label widget."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        label = app.query_one("#command-label", Static)
        assert label is not None
        assert "No command running" in str(label._Static__content)


@pytest.mark.asyncio
async def test_set_command_updates_label(project_dir: Path) -> None:
    """set_command() should update the label text and add the active class."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        log_panel = app.query_one("#log-panel", LogPanelWidget)
        log_panel.set_command("gmail-ingestor fetch --label INBOX")
        await pilot.pause()

        label = app.query_one("#command-label", Static)
        assert "Running: gmail-ingestor fetch --label INBOX" in str(label._Static__content)
        assert label.has_class("active")


@pytest.mark.asyncio
async def test_complete_command_shows_completed(project_dir: Path) -> None:
    """complete_command() should show 'Completed: {name}' with .completed class."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        log_panel = app.query_one("#log-panel", LogPanelWidget)

        log_panel.set_command("gmail-ingestor discover --label INBOX")
        await pilot.pause()
        log_panel.complete_command()
        await pilot.pause()

        label = app.query_one("#command-label", Static)
        assert "Completed: gmail-ingestor discover --label INBOX" in str(label._Static__content)
        assert label.has_class("completed")
        assert not label.has_class("active")


@pytest.mark.asyncio
async def test_complete_command_failed_shows_error(project_dir: Path) -> None:
    """complete_command('Failed') should show 'Failed: {name}' with .error class."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        log_panel = app.query_one("#log-panel", LogPanelWidget)

        log_panel.set_command("gmail-ingestor fetch --label INBOX")
        await pilot.pause()
        log_panel.complete_command("Failed")
        await pilot.pause()

        label = app.query_one("#command-label", Static)
        assert "Failed: gmail-ingestor fetch --label INBOX" in str(label._Static__content)
        assert label.has_class("error")
        assert not label.has_class("active")
        assert not label.has_class("completed")


@pytest.mark.asyncio
async def test_complete_command_cancelled_shows_error(project_dir: Path) -> None:
    """complete_command('Cancelled') should show 'Cancelled: {name}' with .error class."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        log_panel = app.query_one("#log-panel", LogPanelWidget)

        log_panel.set_command("gmail-ingestor fetch-pending --batch-size 50")
        await pilot.pause()
        log_panel.complete_command("Cancelled")
        await pilot.pause()

        label = app.query_one("#command-label", Static)
        assert "Cancelled: gmail-ingestor fetch-pending --batch-size 50" in str(label._Static__content)
        assert label.has_class("error")
        assert not label.has_class("active")


@pytest.mark.asyncio
async def test_clear_command_resets_label(project_dir: Path) -> None:
    """clear_command() should reset the label to idle state (e.g. Clear button)."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        log_panel = app.query_one("#log-panel", LogPanelWidget)

        # Set, complete, then clear
        log_panel.set_command("discover")
        await pilot.pause()
        log_panel.complete_command()
        await pilot.pause()
        log_panel.clear_command()
        await pilot.pause()

        label = app.query_one("#command-label", Static)
        assert "No command running" in str(label._Static__content)
        assert not label.has_class("active")
        assert not label.has_class("completed")
        assert not label.has_class("error")


# --- _build_cli_command() unit tests ---


# Default params used as a baseline — all values at their defaults
_DEFAULT_PARAMS = {
    "label_id": None,
    "query": None,
    "limit": None,
    "offset": 0,
    "batch_size": None,
    "force_full_sync": False,
}


def test_build_cli_discover_with_label() -> None:
    """discover with a label should produce: gmail-ingestor discover --label INBOX"""
    params = {**_DEFAULT_PARAMS, "label_id": "INBOX"}
    assert _build_cli_command("discover", params) == "gmail-ingestor discover --label INBOX"


def test_build_cli_fetch_all_flags() -> None:
    """full_fetch with all flags set should include every relevant flag."""
    params = {
        "label_id": "INBOX",
        "query": "from:test",
        "force_full_sync": True,
        "limit": 100,
        "offset": 10,
        "batch_size": 50,
    }
    result = _build_cli_command("full_fetch", params)
    assert result == (
        'gmail-ingestor fetch --label INBOX --query from:test '
        '--full-sync --limit 100 --offset 10 --batch-size 50'
    )


def test_build_cli_fetch_pending_batch_size() -> None:
    """fetch_pending with only batch_size should produce a clean command."""
    params = {**_DEFAULT_PARAMS, "batch_size": 50}
    assert _build_cli_command("fetch_pending", params) == "gmail-ingestor fetch-pending --batch-size 50"


def test_build_cli_convert_pending_no_params() -> None:
    """convert_pending with all defaults should produce just the subcommand."""
    assert _build_cli_command("convert_pending", _DEFAULT_PARAMS) == "gmail-ingestor convert-pending"


def test_build_cli_retry_no_flags() -> None:
    """retry_failed never has flags."""
    assert _build_cli_command("retry_failed", _DEFAULT_PARAMS) == "gmail-ingestor retry"
    # Even with non-default values, retry ignores all params
    params = {**_DEFAULT_PARAMS, "label_id": "INBOX", "limit": 100}
    assert _build_cli_command("retry_failed", params) == "gmail-ingestor retry"


def test_build_cli_none_params_skipped() -> None:
    """None values for label, query, limit, batch_size should be omitted."""
    assert _build_cli_command("discover", _DEFAULT_PARAMS) == "gmail-ingestor discover"


def test_build_cli_offset_zero_skipped() -> None:
    """offset=0 (the default) should be omitted from the command."""
    params = {**_DEFAULT_PARAMS, "label_id": "INBOX", "offset": 0}
    result = _build_cli_command("discover", params)
    assert "--offset" not in result


def test_build_cli_offset_nonzero_included() -> None:
    """Non-zero offset should appear in the command."""
    params = {**_DEFAULT_PARAMS, "offset": 25}
    result = _build_cli_command("fetch_pending", params)
    assert "--offset 25" in result


def test_build_cli_full_sync_false_omitted() -> None:
    """force_full_sync=False should not include --full-sync flag."""
    params = {**_DEFAULT_PARAMS, "label_id": "INBOX", "force_full_sync": False}
    result = _build_cli_command("full_fetch", params)
    assert "--full-sync" not in result


def test_build_cli_label_with_spaces_quoted() -> None:
    """Label values with spaces or commas should be quoted."""
    params = {**_DEFAULT_PARAMS, "label_id": "INBOX, SENT"}
    result = _build_cli_command("full_fetch", params)
    assert '--label "INBOX, SENT"' in result


def test_build_cli_query_with_spaces_quoted() -> None:
    """Query values with spaces should be quoted."""
    params = {**_DEFAULT_PARAMS, "query": "from:user subject:hello world"}
    result = _build_cli_command("discover", params)
    assert '--query "from:user subject:hello world"' in result
