"""Main Textual application for the Gmail Ingestor TUI."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Footer, Header, Input, TabbedContent, TabPane
from textual.worker import get_current_worker

from gmail_ingestor.config.settings import GmailIngestorSettings
from gmail_ingestor.core.exceptions import RateLimitError
from gmail_ingestor.core.models import FetchProgress
from gmail_ingestor.pipeline.ingestor import EmailIngestor

from ingestor_tui.widgets.confirm_dialog import ConfirmDialog
from ingestor_tui.widgets.dashboard import DashboardWidget
from ingestor_tui.widgets.labels import LabelsSelected, LabelsWidget
from ingestor_tui.widgets.log_panel import LogPanelWidget
from ingestor_tui.widgets.operations import OperationsWidget

logger = logging.getLogger(__name__)

APP_CSS = """
Screen {
    layout: vertical;
}

TabbedContent {
    height: 1fr;
}

.section-title {
    text-style: bold;
    margin: 1 0 0 0;
    color: $text;
}

#card-pending {
    background: $primary-darken-2;
}
#card-fetched {
    background: $secondary-darken-2;
}
#card-converted {
    background: $success-darken-2;
}
#card-failed {
    background: $error-darken-2;
}
#card-total {
    background: $surface-lighten-1;
}
"""


class IngestorApp(App):
    """Textual TUI for the Gmail Ingestor pipeline."""

    TITLE = "Gmail Ingestor TUI"
    CSS = APP_CSS

    BINDINGS = [
        Binding("d", "switch_tab('tab-dashboard')", "Dashboard", show=True),
        Binding("l", "switch_tab('tab-labels')", "Labels", show=True),
        Binding("o", "switch_tab('tab-operations')", "Operations", show=True),
        Binding("g", "switch_tab('tab-log')", "Log", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, project_dir: Path) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._settings: GmailIngestorSettings | None = None
        self._ingestor: EmailIngestor | None = None
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Dashboard", id="tab-dashboard"):
                yield DashboardWidget(id="dashboard")
            with TabPane("Labels", id="tab-labels"):
                yield LabelsWidget(id="labels")
            with TabPane("Operations", id="tab-operations"):
                yield OperationsWidget(id="operations")
            with TabPane("Log", id="tab-log"):
                yield LogPanelWidget(id="log-panel")
        yield Footer()

    def on_mount(self) -> None:
        os.chdir(self._project_dir)
        logger.info("Working directory: %s", self._project_dir)

        self.query_one("#input-project-dir", Input).value = str(self._project_dir)

        try:
            self._settings = GmailIngestorSettings()
            dashboard = self.query_one("#dashboard", DashboardWidget)
            dashboard.set_db_path(self._settings.database_path)
            dashboard.show_config(
                {
                    "Label": self._settings.label,
                    "Batch Size": self._settings.batch_size,
                    "Max Results/Page": self._settings.max_results_per_page,
                    "Database": str(self._settings.database_path),
                    "Output (Markdown)": str(self._settings.output_markdown_dir),
                    "Output (Raw)": str(self._settings.output_raw_dir),
                    "Credentials": str(self._settings.credentials_path),
                    "Log Level": self._settings.log_level,
                    "Max Retries": self._settings.max_retries,
                    "Initial Backoff (s)": self._settings.initial_backoff_seconds,
                    "Max Backoff (s)": self._settings.max_backoff_seconds,
                    "Inter-Batch Delay (s)": self._settings.inter_batch_delay_seconds,
                    "Inter-Page Delay (s)": self._settings.inter_page_delay_seconds,
                    "Num Retries": self._settings.num_retries,
                }
            )
            dashboard.refresh_status()
        except Exception as e:
            logger.error("Failed to load settings: %s", e)
            self.notify(f"Settings error: {e}", severity="error")

        self._refresh_timer = self.set_interval(5.0, self._auto_refresh_dashboard)

    def _auto_refresh_dashboard(self) -> None:
        try:
            self.query_one("#dashboard", DashboardWidget).refresh_status()
        except Exception:
            pass

    def _get_ingestor(self) -> EmailIngestor:
        """Lazy-create the EmailIngestor with progress callback."""
        if self._ingestor is None:
            self._ingestor = EmailIngestor(
                settings=self._settings,
                on_progress=self._on_progress,
            )
        return self._ingestor

    def _on_progress(self, progress: FetchProgress) -> None:
        """Progress callback — called from worker thread."""
        ops = self.query_one("#operations", OperationsWidget)
        stage = progress.current_stage

        if stage == "discovery":
            self.call_from_thread(
                ops.update_progress, f"Discovery", progress.ids_discovered, 0
            )
        elif stage == "fetch":
            self.call_from_thread(
                ops.update_progress,
                "Fetching",
                progress.messages_fetched,
                progress.ids_discovered or 0,
            )
        elif stage == "convert":
            fetched = progress.messages_fetched or 0
            self.call_from_thread(
                ops.update_progress,
                "Converting",
                progress.messages_converted,
                fetched,
            )
        elif stage == "complete":
            self.call_from_thread(
                ops.update_progress,
                "Complete",
                progress.messages_converted,
                progress.messages_converted,
            )
        elif stage.startswith("error"):
            self.call_from_thread(ops.update_progress, stage, 0, 0)

        self.call_from_thread(
            self.query_one("#dashboard", DashboardWidget).refresh_status
        )

    def on_labels_selected(self, event: LabelsSelected) -> None:
        """Copy selected label IDs to the Operations input and switch tab."""
        self.query_one("#input-label", Input).value = event.label_string
        self.action_switch_tab("tab-operations")
        count = len(event.label_ids)
        self.notify(f"Copied {count} label{'s' if count != 1 else ''} to Operations")

    def action_switch_tab(self, tab_id: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab_id

    # --- Helpers ---

    @staticmethod
    def _parse_labels(label_str: str | None) -> list[str | None]:
        """Split a comma-separated label string into individual labels."""
        if not label_str:
            return [None]
        parts = [s.strip() for s in label_str.split(",") if s.strip()]
        return parts if parts else [None]

    # --- Operation handlers ---

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id

        # Operations that need confirmation
        operation_map = {
            "btn-full-fetch": "full_fetch",
            "btn-discover": "discover",
            "btn-fetch-pending": "fetch_pending",
            "btn-convert-pending": "convert_pending",
            "btn-retry-failed": "retry_failed",
        }

        if button_id in operation_map:
            operation = operation_map[button_id]
            self.push_screen(
                ConfirmDialog(f"Run [bold]{operation}[/bold]?"),
                callback=lambda confirmed, op=operation: self._run_operation(op) if confirmed else None,
            )
        elif button_id == "btn-stop":
            self._cancel_operation()
        elif button_id == "btn-refresh-labels":
            self._run_labels_refresh()
        elif button_id == "btn-apply-project-dir":
            self._apply_project_dir()

    def _cancel_operation(self) -> None:
        """Cancel the currently running pipeline worker."""
        for worker in self.workers:
            if worker.group == "pipeline" and worker.is_running:
                worker.cancel()
                log_panel = self.query_one("#log-panel", LogPanelWidget)
                log_panel.write(
                    "[yellow bold]Stop requested — will stop after current batch[/yellow bold]"
                )
                return
        self.notify("No operation is running", severity="warning")

    def _apply_project_dir(self) -> None:
        """Apply a new project directory from the Dashboard input."""
        new_dir_str = self.query_one("#input-project-dir", Input).value.strip()
        if not new_dir_str:
            self.notify("Project directory cannot be empty", severity="error")
            return

        new_dir = Path(new_dir_str).resolve()
        if not new_dir.is_dir():
            self.notify(f"Not a valid directory: {new_dir}", severity="error")
            return

        # Check if an operation is running
        for worker in self.workers:
            if worker.group == "pipeline" and worker.is_running:
                self.notify("Cannot change directory while an operation is running", severity="warning")
                return

        os.chdir(new_dir)
        self._project_dir = new_dir
        self._ingestor = None
        logger.info("Project directory changed to: %s", new_dir)

        try:
            self._settings = GmailIngestorSettings()
            dashboard = self.query_one("#dashboard", DashboardWidget)
            dashboard.set_db_path(self._settings.database_path)
            dashboard.show_config(
                {
                    "Label": self._settings.label,
                    "Batch Size": self._settings.batch_size,
                    "Max Results/Page": self._settings.max_results_per_page,
                    "Database": str(self._settings.database_path),
                    "Output (Markdown)": str(self._settings.output_markdown_dir),
                    "Output (Raw)": str(self._settings.output_raw_dir),
                    "Credentials": str(self._settings.credentials_path),
                    "Log Level": self._settings.log_level,
                    "Max Retries": self._settings.max_retries,
                    "Initial Backoff (s)": self._settings.initial_backoff_seconds,
                    "Max Backoff (s)": self._settings.max_backoff_seconds,
                    "Inter-Batch Delay (s)": self._settings.inter_batch_delay_seconds,
                    "Inter-Page Delay (s)": self._settings.inter_page_delay_seconds,
                    "Num Retries": self._settings.num_retries,
                }
            )
            dashboard.refresh_status()
            self.notify(f"Project directory: {new_dir}", severity="information")
        except Exception as e:
            logger.error("Failed to reload settings: %s", e)
            self.notify(f"Settings error: {e}", severity="error")

    def _run_operation(self, operation: str) -> None:
        ops = self.query_one("#operations", OperationsWidget)
        ops.set_running(True)
        self.action_switch_tab("tab-log")
        self._do_operation(operation)

    @work(thread=True, exclusive=True, group="pipeline")
    def _do_operation(self, operation: str) -> None:
        ops = self.query_one("#operations", OperationsWidget)
        log_panel = self.query_one("#log-panel", LogPanelWidget)
        worker = get_current_worker()

        try:
            ingestor = self._get_ingestor()
            params = self.call_from_thread(ops.get_params)
            labels = self._parse_labels(params.get("label_id"))

            self.call_from_thread(
                log_panel.write, f"\n[bold]Starting: {operation}[/bold]"
            )

            if operation == "full_fetch":
                for label_id in labels:
                    if worker.is_cancelled:
                        self.call_from_thread(log_panel.write, "[yellow]Cancelled[/yellow]")
                        break
                    run_params = {**params, "label_id": label_id}
                    if label_id:
                        self.call_from_thread(
                            log_panel.write, f"[dim]Label: {label_id}[/dim]"
                        )
                    result = ingestor.run(**run_params)
                    self.call_from_thread(
                        log_panel.write,
                        f"[green]Complete — discovered={result.ids_discovered} "
                        f"fetched={result.messages_fetched} "
                        f"converted={result.messages_converted} "
                        f"failed={result.messages_failed}[/green]",
                    )

            elif operation == "discover":
                for label_id in labels:
                    if worker.is_cancelled:
                        self.call_from_thread(log_panel.write, "[yellow]Cancelled[/yellow]")
                        break
                    if label_id:
                        self.call_from_thread(
                            log_panel.write, f"[dim]Label: {label_id}[/dim]"
                        )
                    count = ingestor.run_discovery(
                        label_id=label_id,
                        query=params["query"],
                        limit=params["limit"],
                        offset=params["offset"],
                    )
                    self.call_from_thread(
                        log_panel.write, f"[green]Discovered {count} new message IDs[/green]"
                    )

            elif operation == "fetch_pending":
                if worker.is_cancelled:
                    self.call_from_thread(log_panel.write, "[yellow]Cancelled[/yellow]")
                else:
                    count = ingestor.run_fetch_pending(
                        limit=params["limit"],
                        offset=params["offset"],
                        batch_size=params["batch_size"],
                    )
                    self.call_from_thread(
                        log_panel.write, f"[green]Fetched {count} messages[/green]"
                    )

            elif operation == "convert_pending":
                if worker.is_cancelled:
                    self.call_from_thread(log_panel.write, "[yellow]Cancelled[/yellow]")
                else:
                    count = ingestor.run_convert_pending(
                        limit=params["limit"],
                        offset=params["offset"],
                        batch_size=params["batch_size"],
                    )
                    self.call_from_thread(
                        log_panel.write, f"[green]Converted {count} messages[/green]"
                    )

            elif operation == "retry_failed":
                if worker.is_cancelled:
                    self.call_from_thread(log_panel.write, "[yellow]Cancelled[/yellow]")
                else:
                    count = ingestor.retry_failed()
                    self.call_from_thread(
                        log_panel.write, f"[green]Reset {count} failed messages to pending[/green]"
                    )

            if not worker.is_cancelled:
                self.call_from_thread(self.notify, f"{operation} completed", severity="information")

        except RateLimitError as e:
            self.call_from_thread(
                log_panel.write,
                f"[red bold]Rate limit exceeded: {e}[/red bold]\n"
                "[yellow]Try waiting a few minutes before retrying, or adjust "
                "rate-limit settings (max_retries, backoff, delays) in your .env file.[/yellow]",
            )
            self.call_from_thread(
                self.notify, "Rate limit exceeded — wait before retrying", severity="error"
            )
            logger.warning("Operation %s hit rate limit: %s", operation, e)

        except Exception as e:
            self.call_from_thread(
                log_panel.write, f"[red bold]Error: {e}[/red bold]"
            )
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")
            logger.exception("Operation %s failed", operation)

        finally:
            self.call_from_thread(ops.set_running, False)
            self.call_from_thread(
                self.query_one("#dashboard", DashboardWidget).refresh_status
            )

    @work(thread=True, exclusive=True, group="labels")
    def _run_labels_refresh(self) -> None:
        labels_widget = self.query_one("#labels", LabelsWidget)
        self.call_from_thread(labels_widget.set_loading, True)

        try:
            ingestor = self._get_ingestor()
            labels = ingestor.list_labels()
            self.call_from_thread(labels_widget.populate, labels)
            self.call_from_thread(
                self.notify, f"Loaded {len(labels)} labels", severity="information"
            )
        except Exception as e:
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")
            logger.exception("Failed to refresh labels")
        finally:
            self.call_from_thread(labels_widget.set_loading, False)

    def on_unmount(self) -> None:
        if self._ingestor:
            self._ingestor.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Gmail Ingestor TUI")
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path("../gmail-ingestor"),
        help="Path to the gmail-ingestor project directory (default: ../gmail-ingestor)",
    )
    args = parser.parse_args()

    project_dir = args.project_dir.resolve()
    if not project_dir.is_dir():
        print(f"Error: {project_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    app = IngestorApp(project_dir)
    app.run()


if __name__ == "__main__":
    main()
