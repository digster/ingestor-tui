"""Log panel widget with a custom logging handler that pipes to RichLog."""

from __future__ import annotations

import logging
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, RichLog, Static


class TUILogHandler(logging.Handler):
    """Routes Python log records into a Textual RichLog widget.

    Uses app.call_from_thread() so it's safe from worker threads.
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
            app.call_from_thread(self._rich_log.write, msg)
        except Exception:
            self.handleError(record)


class LogPanelWidget(Vertical):
    """Panel displaying real-time log output from the gmail_ingestor logger."""

    DEFAULT_CSS = """
    LogPanelWidget {
        height: 1fr;
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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._handler: TUILogHandler | None = None

    def compose(self) -> ComposeResult:
        yield Static("Command Output", classes="section-title")
        yield RichLog(highlight=True, markup=True, wrap=True, id="log-output")
        with Vertical(classes="log-toolbar"):
            yield Button("Clear", id="btn-clear-log", variant="default")

    def on_mount(self) -> None:
        rich_log = self.query_one("#log-output", RichLog)
        self._handler = TUILogHandler(rich_log)
        self._handler.setLevel(logging.DEBUG)

        ingestor_logger = logging.getLogger("gmail_ingestor")
        ingestor_logger.addHandler(self._handler)
        ingestor_logger.setLevel(logging.DEBUG)

        rich_log.write(f"[dim]{datetime.now():%H:%M:%S} Log panel ready[/dim]")

    def on_unmount(self) -> None:
        if self._handler:
            logging.getLogger("gmail_ingestor").removeHandler(self._handler)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-clear-log":
            self.query_one("#log-output", RichLog).clear()

    def write(self, message: str) -> None:
        """Write a message directly to the log panel."""
        self.query_one("#log-output", RichLog).write(message)
