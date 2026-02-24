"""Gmail labels widget displaying a DataTable of available labels with search filter."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Static


class LabelsWidget(Vertical):
    """Displays Gmail labels in a sortable DataTable with client-side filtering."""

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
        height: auto;
        layout: horizontal;
        align: left middle;
        margin: 0 0 1 0;
    }
    LabelsWidget #labels-filter {
        width: 1fr;
        margin: 0 1 0 0;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._all_labels: list[dict[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Static("Gmail Labels", classes="section-title")
        with Horizontal(classes="labels-toolbar"):
            yield Input(
                placeholder="Filter labels...",
                id="labels-filter",
            )
            yield Button("Refresh Labels", id="btn-refresh-labels", variant="primary")
        table = DataTable(id="labels-table")
        table.add_columns("Label ID", "Name")
        yield table

    def populate(self, labels: list[dict[str, str]]) -> None:
        """Fill the DataTable with label data and store for filtering."""
        self._all_labels = sorted(labels, key=lambda x: x.get("name", ""))
        self._apply_filter()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "labels-filter":
            self._apply_filter()

    def _apply_filter(self) -> None:
        """Filter table rows based on the current filter text."""
        table = self.query_one("#labels-table", DataTable)
        table.clear()
        filter_text = self.query_one("#labels-filter", Input).value.strip().lower()
        for label in self._all_labels:
            label_id = label.get("id", "")
            name = label.get("name", "")
            if not filter_text or filter_text in name.lower() or filter_text in label_id.lower():
                table.add_row(label_id, name)

    def set_loading(self, loading: bool) -> None:
        """Toggle the refresh button state."""
        self.query_one("#btn-refresh-labels", Button).disabled = loading
