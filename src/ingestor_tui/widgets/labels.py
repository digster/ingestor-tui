"""Gmail labels widget displaying a DataTable of available labels."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, DataTable, Static


class LabelsWidget(Vertical):
    """Displays Gmail labels in a sortable DataTable."""

    DEFAULT_CSS = """
    LabelsWidget {
        height: 1fr;
        padding: 1 2;
    }
    LabelsWidget DataTable {
        height: 1fr;
        margin: 1 0;
    }
    LabelsWidget .labels-toolbar {
        height: 3;
        layout: horizontal;
        align: left middle;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Gmail Labels", classes="section-title")
        with Vertical(classes="labels-toolbar"):
            yield Button("Refresh Labels", id="btn-refresh-labels", variant="primary")
        table = DataTable(id="labels-table")
        table.add_columns("Label ID", "Name")
        yield table

    def populate(self, labels: list[dict[str, str]]) -> None:
        """Fill the DataTable with label data."""
        table = self.query_one("#labels-table", DataTable)
        table.clear()
        for label in sorted(labels, key=lambda x: x.get("name", "")):
            table.add_row(label.get("id", ""), label.get("name", ""))

    def set_loading(self, loading: bool) -> None:
        """Toggle the refresh button state."""
        self.query_one("#btn-refresh-labels", Button).disabled = loading
