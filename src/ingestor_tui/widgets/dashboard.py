"""Dashboard widget showing pipeline status, last run, and configuration."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static

from ingestor_tui.db_reader import DBReader


class StatusCard(Static):
    """Single status metric card."""

    DEFAULT_CSS = """
    StatusCard {
        width: 1fr;
        height: 5;
        content-align: center middle;
        text-align: center;
        border: solid $surface-lighten-2;
        margin: 0 1;
    }
    """

    def __init__(self, label: str, value: int = 0, variant: str = "", **kwargs) -> None:
        self._label = label
        self._value = value
        super().__init__(self._format_content(), classes=variant, **kwargs)

    def _format_content(self) -> str:
        return f"{self._value}\n[dim]{self._label}[/dim]"

    def update_value(self, value: int) -> None:
        self._value = value
        self.update(self._format_content())


class DashboardWidget(Vertical):
    """Dashboard showing status overview, last run info, and config."""

    DEFAULT_CSS = """
    DashboardWidget {
        height: 1fr;
        padding: 1 2;
    }
    DashboardWidget .section-title {
        text-style: bold;
        margin: 1 0 0 0;
        color: $text;
    }
    DashboardWidget .status-row {
        height: 5;
        margin: 1 0;
    }
    DashboardWidget .info-panel {
        height: auto;
        max-height: 12;
        border: solid $surface-lighten-2;
        padding: 1;
        margin: 0 0 1 0;
    }
    DashboardWidget .project-dir-row {
        height: auto;
        layout: horizontal;
        margin: 1 0;
    }
    DashboardWidget #input-project-dir {
        width: 1fr;
        margin: 0 1 0 0;
    }
    """

    def __init__(self, db_path: Path | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._db_reader = DBReader(db_path) if db_path else None

    def compose(self) -> ComposeResult:
        yield Static("Project Directory", classes="section-title")
        with Horizontal(classes="project-dir-row"):
            yield Input(placeholder="Path to project directory", id="input-project-dir")
            yield Button("Apply", id="btn-apply-project-dir", variant="primary")

        yield Static("Status Overview", classes="section-title")
        with Horizontal(classes="status-row"):
            yield StatusCard("Pending", variant="card-pending", id="card-pending")
            yield StatusCard("Fetched", variant="card-fetched", id="card-fetched")
            yield StatusCard("Converted", variant="card-converted", id="card-converted")
            yield StatusCard("Failed", variant="card-failed", id="card-failed")
            yield StatusCard("Total", variant="card-total", id="card-total")

        yield Static("Last Fetch Run", classes="section-title")
        yield Static("No runs yet", id="last-run-info", classes="info-panel")

        yield Static("Sync State", classes="section-title")
        yield Static("No sync data", id="sync-state-info", classes="info-panel")

        yield Static("Configuration", classes="section-title")
        yield Static("Not loaded", id="config-info", classes="info-panel")

    def set_db_path(self, db_path: Path) -> None:
        self._db_reader = DBReader(db_path)

    def refresh_status(self) -> None:
        """Refresh all dashboard data from DB."""
        if not self._db_reader or not self._db_reader.exists:
            return

        counts = self._db_reader.count_by_status()
        total = sum(counts.values())

        self.query_one("#card-pending", StatusCard).update_value(counts.get("pending", 0))
        self.query_one("#card-fetched", StatusCard).update_value(counts.get("fetched", 0))
        self.query_one("#card-converted", StatusCard).update_value(counts.get("converted", 0))
        self.query_one("#card-failed", StatusCard).update_value(counts.get("failed", 0))
        self.query_one("#card-total", StatusCard).update_value(total)

        last_run = self._db_reader.last_run()
        if last_run:
            info = (
                f"Run #{last_run['run_id']}  |  Label: {last_run['label_id']}\n"
                f"Started: {last_run['started_at']}\n"
                f"Completed: {last_run.get('completed_at', 'in progress')}\n"
                f"Discovered: {last_run.get('ids_discovered', 0)}  "
                f"Fetched: {last_run.get('messages_fetched', 0)}  "
                f"Converted: {last_run.get('messages_converted', 0)}  "
                f"Failed: {last_run.get('messages_failed', 0)}"
            )
            self.query_one("#last-run-info", Static).update(info)

        # Sync state
        sync_data = self._db_reader.get_sync_state()
        if sync_data:
            lines = []
            for row in sync_data:
                name = row.get("label_name") or row["label_id"]
                history_id = row.get("history_id", "—")
                updated = row.get("updated_at", "—")
                lines.append(f"{name}  |  history_id: {history_id}  |  updated: {updated}")
            self.query_one("#sync-state-info", Static).update("\n".join(lines))
        else:
            self.query_one("#sync-state-info", Static).update("No sync data")

    def show_config(self, settings_dict: dict) -> None:
        """Display configuration values."""
        lines = [f"{k}: {v}" for k, v in settings_dict.items()]
        self.query_one("#config-info", Static).update("\n".join(lines))
