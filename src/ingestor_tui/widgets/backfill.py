"""Backfill widget — pick a mapped label, scan its archive, fill the gaps.

Two-table layout: the mappings on top (what can be backfilled, and what state
each is in), the scan results below (what is missing and what we already hold).
Scan is deliberately a separate button from Backfill — reading an archive is
free and reversible, writing 80 files into the corpus is not, so the operator
always gets to look before committing.

Prune follows the same rule in the other direction. It removes what earlier
runs wrote, so a label can be regenerated after the extractor changes. The
results table first shows exactly which articles would go, and the app then
asks for confirmation before anything is deleted.

Selection follows the LabelsWidget pattern (row click toggles, action bar
reflects it) so the two tables behave the same way.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Checkbox, DataTable, Input, Label, ProgressBar, Static

from ingestor_tui.backfill.mappings import BackfillMapping
from ingestor_tui.backfill.models import ScanEntry
from ingestor_tui.backfill.prune import PruneResult


class BackfillRequested(Message):
    """Posted when the user starts a scan, a backfill run, or a prune."""

    def __init__(self, label_name: str, operation: str) -> None:
        self.label_name = label_name
        self.operation = operation  # "scan" | "backfill" | "prune"
        super().__init__()


class BackfillWidget(Vertical):
    """Mapping browser, scan preview and backfill controls."""

    DEFAULT_CSS = """
    BackfillWidget {
        height: 1fr;
        padding: 1 2;
    }
    BackfillWidget .backfill-toolbar {
        height: auto;
        layout: horizontal;
        margin: 0 0 1 0;
    }
    BackfillWidget .backfill-toolbar Button {
        margin: 0 1 0 0;
    }
    BackfillWidget .backfill-options {
        height: auto;
        layout: horizontal;
        margin: 0 0 1 0;
    }
    BackfillWidget .backfill-options Label {
        height: 3;
        width: 8;
        content-align: left middle;
    }
    BackfillWidget #input-backfill-limit {
        width: 14;
        margin: 0 2 0 0;
    }
    BackfillWidget #cb-backfill-dry-run {
        height: 3;
        content-align: left middle;
    }
    BackfillWidget #mappings-table {
        height: 8;
        margin: 0 0 1 0;
    }
    BackfillWidget #scan-table {
        height: 1fr;
        margin: 0 0 1 0;
    }
    BackfillWidget #backfill-selection {
        width: auto;
        margin: 0 2 0 0;
        color: $text-muted;
    }
    BackfillWidget #backfill-summary {
        height: auto;
        color: $text-muted;
        margin: 0 0 1 0;
    }
    BackfillWidget #backfill-stage {
        height: 1;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._mappings: dict[str, BackfillMapping] = {}
        self._selected: str | None = None
        self._entries: list[ScanEntry] = []

    def compose(self) -> ComposeResult:
        yield Static("Archive Mappings", classes="section-title")

        with Horizontal(classes="backfill-toolbar"):
            yield Static("no mapping selected", id="backfill-selection")
            yield Button("Reload Mappings", id="btn-backfill-reload", variant="default")
            yield Button("Scan", id="btn-backfill-scan", variant="primary", disabled=True)
            yield Button("Backfill", id="btn-backfill-run", variant="success", disabled=True)
            # Warning rather than error: Stop already owns red on this bar, and
            # the confirmation dialog is where the destructive styling belongs.
            yield Button("Prune", id="btn-backfill-prune", variant="warning", disabled=True)
            yield Button("Stop", id="btn-backfill-stop", variant="error", disabled=True)

        with Horizontal(classes="backfill-options"):
            yield Label("Limit")
            yield Input(placeholder="All", id="input-backfill-limit", type="integer")
            yield Checkbox("Dry run", id="cb-backfill-dry-run")

        mappings_table = DataTable(id="mappings-table", cursor_type="row")
        mappings_table.add_column("", key="col-check", width=3)
        mappings_table.add_column("Label", key="col-label")
        mappings_table.add_column("Mode", key="col-mode", width=10)
        mappings_table.add_column("Archive", key="col-archive")
        mappings_table.add_column("State", key="col-state")
        yield mappings_table

        yield Static("Scan Results", classes="section-title")
        yield Static("Run a scan to see which articles are missing.", id="backfill-summary")

        scan_table = DataTable(id="scan-table", cursor_type="row")
        scan_table.add_column("Status", key="col-status", width=9)
        scan_table.add_column("Date", key="col-date", width=12)
        scan_table.add_column("Title", key="col-title")
        scan_table.add_column("Detail", key="col-detail")
        yield scan_table

        yield Static("Idle", id="backfill-stage")
        yield ProgressBar(total=100, show_eta=False, id="backfill-progress")

    # --- population ---

    def populate_mappings(
        self,
        mappings: dict[str, BackfillMapping],
        state: dict[str, dict[str, int]] | None = None,
    ) -> None:
        """Fill the mappings table. ``state`` holds per-label status counts."""
        self._mappings = mappings
        counts = state or {}

        table = self.query_one("#mappings-table", DataTable)
        table.clear()
        for name, mapping in mappings.items():
            summary = counts.get(name) or {}
            state_text = (
                ", ".join(f"{k}={v}" for k, v in sorted(summary.items())) if summary else "—"
            )
            check = "✓" if name == self._selected else ""
            # Multi-source labels show the primary archive plus a count, so
            # the row stays one line while still admitting the others exist.
            archive_text = mapping.archive_url
            if mapping.is_multi_source:
                archive_text += f"  (+{len(mapping.sources) - 1} more)"
            table.add_row(
                check,
                name,
                mapping.listing.mode,
                archive_text,
                state_text,
                key=name,
            )

        # A selection that no longer exists after a reload must not linger,
        # or Scan would dispatch against a label with no mapping.
        if self._selected not in mappings:
            self._selected = None
        self._update_selection_ui()

    def populate_scan(self, entries: list[ScanEntry]) -> None:
        """Fill the results table, missing entries first."""
        self._entries = entries
        table = self.query_one("#scan-table", DataTable)
        table.clear()

        ordered = sorted(entries, key=lambda e: (not e.is_missing, self._entry_date(e)))
        for entry in ordered:
            status = "MISSING" if entry.is_missing else "have"
            detail = "" if entry.is_missing else entry.match_reason
            table.add_row(
                status,
                self._entry_date(entry) or "—",
                entry.ref.title,
                detail,
                key=entry.article_id,
            )

        missing = sum(1 for e in entries if e.is_missing)
        self.query_one("#backfill-summary", Static).update(
            f"{len(entries)} listed · {len(entries) - missing} already held · "
            f"[bold]{missing} missing[/bold]"
        )

    def update_entry_status(self, article_id: str, status: str, detail: str = "") -> None:
        """Update one row as a run progresses. Ignores unknown rows."""
        table = self.query_one("#scan-table", DataTable)
        try:
            table.update_cell(article_id, "col-status", status)
            if detail:
                table.update_cell(article_id, "col-detail", detail)
        except Exception:
            # The row may have been cleared by a concurrent rescan.
            pass

    def populate_prune(self, result: PruneResult) -> None:
        """Show a prune's targets in the results table: a preview or what went.

        Reuses the scan table because a prune preview answers the same kind of
        question a scan does — which articles, and in what state — just before
        a destructive step instead of a constructive one.
        """
        self._entries = []  # the table no longer shows scan entries
        table = self.query_one("#scan-table", DataTable)
        table.clear()

        status = "prune" if result.dry_run else "removed"
        for target in result.targets:
            if target.is_present:
                detail = (
                    f"{target.status} · {len(target.files)} file(s), "
                    f"{len(target.directories)} folder(s)"
                )
            else:
                # A row whose files are already gone (a partial prune, or a
                # manual deletion). Its database row is still cleared.
                detail = f"{target.status} · nothing on disk"
            table.add_row(
                status,
                target.published_at or "—",
                target.title,
                detail,
                key=target.article_id,
            )

        summary = self.query_one("#backfill-summary", Static)
        if not result.targets:
            summary.update(f"{result.label}: nothing backfilled — nothing to prune")
            return

        counts = (
            f"{result.files_removed} file(s), "
            f"{result.directories_removed} newsletter folder(s), "
            f"{result.rows_removed} database row(s)"
        )
        if result.dry_run:
            summary.update(
                f"Prune preview · {len(result.targets)} backfilled article(s) · "
                f"[bold]would remove {counts}[/bold]"
            )
        else:
            summary.update(
                f"Pruned {len(result.targets)} article(s) · [bold]removed {counts}[/bold]"
            )

    @staticmethod
    def _entry_date(entry: ScanEntry) -> str:
        return entry.ref.published_at.strftime("%Y-%m-%d") if entry.ref.published_at else ""

    # --- selection ---

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "mappings-table":
            return
        event.stop()
        name = str(event.row_key.value)
        self._selected = None if self._selected == name else name

        table = self.query_one("#mappings-table", DataTable)
        for key in self._mappings:
            table.update_cell(key, "col-check", "✓" if key == self._selected else "")
        self._update_selection_ui()

    def _update_selection_ui(self) -> None:
        has_selection = self._selected is not None
        self.query_one("#backfill-selection", Static).update(
            self._selected or "no mapping selected"
        )
        self.query_one("#btn-backfill-scan", Button).disabled = not has_selection
        self.query_one("#btn-backfill-run", Button).disabled = not has_selection
        self.query_one("#btn-backfill-prune", Button).disabled = not has_selection

    @property
    def selected_label(self) -> str | None:
        return self._selected

    # --- controls ---

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-backfill-scan" and self._selected:
            event.stop()
            self.post_message(BackfillRequested(self._selected, "scan"))
        elif event.button.id == "btn-backfill-run" and self._selected:
            event.stop()
            self.post_message(BackfillRequested(self._selected, "backfill"))
        elif event.button.id == "btn-backfill-prune" and self._selected:
            event.stop()
            self.post_message(BackfillRequested(self._selected, "prune"))

    def get_params(self) -> dict:
        """Collect run parameters from the inputs."""
        limit_str = self.query_one("#input-backfill-limit", Input).value.strip()
        return {
            "label_name": self._selected,
            "limit": int(limit_str) if limit_str else None,
            "dry_run": self.query_one("#cb-backfill-dry-run", Checkbox).value,
        }

    def set_running(self, running: bool, *, cancellable: bool = True) -> None:
        """Toggle controls for the duration of a run; Stop is the inverse.

        ``cancellable=False`` keeps Stop disabled for operations that do not
        check for cancellation (prune), so the button never promises a stop
        that will not happen.
        """
        has_selection = self._selected is not None
        self.query_one("#btn-backfill-stop", Button).disabled = not (running and cancellable)
        self.query_one("#btn-backfill-reload", Button).disabled = running
        for button_id in ("#btn-backfill-scan", "#btn-backfill-run", "#btn-backfill-prune"):
            self.query_one(button_id, Button).disabled = running or not has_selection

    def update_progress(self, stage: str, current: int = 0, total: int = 0) -> None:
        """Update the stage label and progress bar."""
        bar = self.query_one("#backfill-progress", ProgressBar)
        label = self.query_one("#backfill-stage", Static)
        if total > 0:
            bar.update(total=total, progress=current)
            label.update(f"[bold]{stage}[/bold] — {current}/{total}")
        else:
            bar.update(total=100, progress=0)
            label.update(f"[bold]{stage}[/bold]")

    def reset_progress(self) -> None:
        self.query_one("#backfill-progress", ProgressBar).update(total=100, progress=0)
        self.query_one("#backfill-stage", Static).update("Idle")
