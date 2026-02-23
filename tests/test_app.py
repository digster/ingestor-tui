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
async def test_dashboard_widget_present(project_dir: Path) -> None:
    """Dashboard widget should be mounted."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        dashboard = app.query_one("#dashboard")
        assert dashboard is not None


@pytest.mark.asyncio
async def test_operations_buttons_present(project_dir: Path) -> None:
    """Operations tab should have all pipeline buttons."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        button_ids = {b.id for b in app.query("Button") if b.id and b.id.startswith("btn-")}
        expected = {"btn-full-fetch", "btn-discover", "btn-fetch-pending", "btn-convert-pending", "btn-retry-failed"}
        assert expected.issubset(button_ids)
