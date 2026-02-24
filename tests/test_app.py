"""Smoke tests for IngestorApp — verifies app mounts and tabs render."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestor_tui.app import IngestorApp


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
