"""Gmail labels widget displaying a DataTable of available labels with search filter."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, DataTable, Input, Static


class LabelsSelected(Message):
    """Posted when the user copies selected labels to the Operations tab."""

    def __init__(self, label_ids: list[str], label_string: str) -> None:
        self.label_ids = label_ids
        self.label_string = label_string
        super().__init__()


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
    LabelsWidget .labels-action-bar {
        height: auto;
        layout: horizontal;
        margin: 0 0 1 0;
    }
    LabelsWidget .labels-action-bar #labels-selection-count {
        width: auto;
        margin: 0 2 0 0;
        color: $text-muted;
    }
    LabelsWidget .labels-action-bar Button {
        margin: 0 1 0 0;
    }
    LabelsWidget #labels-filter {
        width: 1fr;
        margin: 0 1 0 0;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._all_labels: list[dict[str, str]] = []
        self._selected_ids: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Static("Gmail Labels", classes="section-title")
        with Horizontal(classes="labels-toolbar"):
            yield Input(
                placeholder="Filter labels...",
                id="labels-filter",
            )
            yield Button("Refresh Labels", id="btn-refresh-labels", variant="primary")
        with Horizontal(classes="labels-action-bar"):
            yield Static("0 selected", id="labels-selection-count")
            yield Button(
                "Copy to Operations",
                id="btn-copy-labels",
                variant="success",
                disabled=True,
            )
            yield Button(
                "Clear Selection",
                id="btn-clear-selection",
                variant="default",
                disabled=True,
            )
        table = DataTable(id="labels-table", cursor_type="row")
        table.add_column("", key="col-check", width=3)
        table.add_column("Label ID", key="col-id")
        table.add_column("Name", key="col-name")
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
                check = "\u2713" if label_id in self._selected_ids else ""
                table.add_row(check, label_id, name, key=label_id)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Toggle selection when a row is clicked/activated."""
        if event.data_table.id != "labels-table":
            return
        row_key = event.row_key
        label_id = str(row_key.value)
        if label_id in self._selected_ids:
            self._selected_ids.discard(label_id)
        else:
            self._selected_ids.add(label_id)
        table = self.query_one("#labels-table", DataTable)
        check = "\u2713" if label_id in self._selected_ids else ""
        table.update_cell(row_key, "col-check", check)
        self._update_selection_ui()

    def _update_selection_ui(self) -> None:
        """Update the selection count badge and button disabled states."""
        count = len(self._selected_ids)
        self.query_one("#labels-selection-count", Static).update(
            f"{count} selected"
        )
        has_selection = count > 0
        self.query_one("#btn-copy-labels", Button).disabled = not has_selection
        self.query_one("#btn-clear-selection", Button).disabled = not has_selection

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-copy-labels":
            event.stop()
            ids = sorted(self._selected_ids)
            self.post_message(LabelsSelected(ids, ", ".join(ids)))
        elif event.button.id == "btn-clear-selection":
            event.stop()
            self._selected_ids.clear()
            self._apply_filter()
            self._update_selection_ui()

    def select_labels(self, label_ids: list[str]) -> None:
        """Replace the current selection with the given label IDs and refresh UI."""
        self._selected_ids = set(label_ids)
        self._apply_filter()
        self._update_selection_ui()

    def set_loading(self, loading: bool) -> None:
        """Toggle the refresh button state."""
        self.query_one("#btn-refresh-labels", Button).disabled = loading
