"""Log panel widget with a custom logging handler that pipes to RichLog."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, RichLog, Static


class TUILogHandler(logging.Handler):
    """Routes Python log records into a Textual RichLog widget.

    Records arrive from two places and must be dispatched differently:

    * **Worker threads** — pipeline and backfill workers. These need
      ``app.call_from_thread`` to hand the write back to the app's thread.
    * **The app's own thread** — anything logged from a message handler or
      ``on_mount``. Textual *rejects* ``call_from_thread`` here ("must run in a
      different thread from the app"), so these must write directly.

    Routing on the calling thread rather than assuming one is what keeps both
    working. This only became reachable once the ``ingestor_tui`` logger was
    captured for backfill; before that every captured record came from a worker.
    """

    def __init__(self, rich_log: RichLog) -> None:
        super().__init__()
        self._rich_log = rich_log
        self.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            app = self._rich_log.app

            # Mirrors Textual's own guard inside call_from_thread. getattr with
            # a default so a rename upstream degrades to the marshalled path
            # rather than raising from inside a log handler.
            if getattr(app, "_thread_id", None) == threading.get_ident():
                self._rich_log.write(msg)
            else:
                app.call_from_thread(self._rich_log.write, msg)
        except Exception:
            self.handleError(record)


class LogPanelWidget(Vertical):
    """Panel displaying real-time log output from the gmail_ingestor logger."""

    DEFAULT_CSS = """
    LogPanelWidget {
        height: 1fr;
    }
    LogPanelWidget #command-label {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    LogPanelWidget #command-label.active {
        color: $accent;
        text-style: bold;
    }
    LogPanelWidget #command-label.completed {
        color: $success;
    }
    LogPanelWidget #command-label.error {
        color: $error;
    }
    LogPanelWidget RichLog {
        height: 1fr;
        border: solid $surface-lighten-2;
        scrollbar-size: 1 1;
    }
    LogPanelWidget .log-toolbar {
        height: 3;
        layout: horizontal;
        align: left middle;
        padding: 0 1;
    }
    """

    # Loggers piped into the RichLog. "gmail_ingestor" carries the Gmail
    # pipeline; "ingestor_tui" carries backfill, whose scraping progress and
    # per-article failures are the only visibility the operator gets into a
    # long archive run.
    CAPTURED_LOGGERS = ("gmail_ingestor", "ingestor_tui")

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._handler: TUILogHandler | None = None
        self._current_command: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("Command Output", classes="section-title")
        yield Static("No command running", id="command-label")
        yield RichLog(highlight=True, markup=True, wrap=True, id="log-output")
        with Vertical(classes="log-toolbar"):
            yield Button("Clear", id="btn-clear-log", variant="default")

    def on_mount(self) -> None:
        rich_log = self.query_one("#log-output", RichLog)
        self._handler = TUILogHandler(rich_log)
        self._handler.setLevel(logging.DEBUG)

        for name in self.CAPTURED_LOGGERS:
            captured = logging.getLogger(name)
            captured.addHandler(self._handler)
            captured.setLevel(logging.DEBUG)

        rich_log.write(f"[dim]{datetime.now():%H:%M:%S} Log panel ready[/dim]")

    def on_unmount(self) -> None:
        if self._handler:
            for name in self.CAPTURED_LOGGERS:
                logging.getLogger(name).removeHandler(self._handler)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-clear-log":
            self.query_one("#log-output", RichLog).clear()

    def set_command(self, command: str) -> None:
        """Display the currently running command above the log output."""
        self._current_command = command
        label = self.query_one("#command-label", Static)
        label.update(f"Running: {command}")
        label.remove_class("completed", "error")
        label.add_class("active")

    def complete_command(self, status: str = "Completed") -> None:
        """Update command label to show completion status.

        Swaps the `.active` class for `.completed` or `.error` depending
        on whether the status indicates a failure.
        """
        command = self._current_command or "unknown"
        label = self.query_one("#command-label", Static)
        label.update(f"{status}: {command}")
        label.remove_class("active")
        # Use error styling for failure/cancellation statuses
        if status in ("Failed", "Cancelled"):
            label.remove_class("completed")
            label.add_class("error")
        else:
            label.remove_class("error")
            label.add_class("completed")

    def clear_command(self) -> None:
        """Reset the command label to idle state (used by Clear button)."""
        self._current_command = None
        label = self.query_one("#command-label", Static)
        label.update("No command running")
        label.remove_class("active", "completed", "error")

    def write(self, message: str) -> None:
        """Write a message directly to the log panel."""
        self.query_one("#log-output", RichLog).write(message)
