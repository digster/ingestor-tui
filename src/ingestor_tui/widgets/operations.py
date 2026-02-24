"""Operations widget with pipeline control buttons, parameter inputs, and progress."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Label, ProgressBar, Static


class OperationStarted(Message):
    """Posted when a pipeline operation starts."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__()


class OperationCompleted(Message):
    """Posted when a pipeline operation completes."""

    def __init__(self, operation: str, success: bool = True, error: str = "") -> None:
        self.operation = operation
        self.success = success
        self.error = error
        super().__init__()


class OperationsWidget(Vertical):
    """Pipeline operation controls with parameter inputs and progress display."""

    DEFAULT_CSS = """
    OperationsWidget {
        height: 1fr;
        padding: 1 2;
    }
    OperationsWidget .section-title {
        text-style: bold;
        margin: 1 0 0 0;
    }
    OperationsWidget .button-row {
        height: auto;
        layout: horizontal;
        margin: 1 0;
    }
    OperationsWidget .button-row Button {
        margin: 0 1 0 0;
    }
    OperationsWidget .params-grid {
        height: auto;
        layout: grid;
        grid-size: 2 5;
        grid-columns: 12 1fr;
        grid-gutter: 1;
        margin: 1 0;
        max-width: 60;
    }
    OperationsWidget .params-grid Label {
        height: 3;
        content-align: left middle;
    }
    OperationsWidget .params-grid Input {
        height: 3;
    }
    OperationsWidget .progress-area {
        height: auto;
        margin: 1 0;
    }
    OperationsWidget .progress-area ProgressBar {
        margin: 1 0;
    }
    OperationsWidget #stage-label {
        height: 1;
        margin: 0 0 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Pipeline Operations", classes="section-title")

        with Horizontal(classes="button-row"):
            yield Button("Full Fetch", id="btn-full-fetch", variant="primary")
            yield Button("Discover", id="btn-discover", variant="default")
            yield Button("Fetch Pending", id="btn-fetch-pending", variant="default")
            yield Button("Convert Pending", id="btn-convert-pending", variant="default")
            yield Button("Retry Failed", id="btn-retry-failed", variant="warning")
            yield Button("Stop", id="btn-stop", variant="error", disabled=True)

        yield Static("Parameters", classes="section-title")
        with Vertical(classes="params-grid"):
            yield Label("Label")
            yield Input(placeholder="INBOX", id="input-label")
            yield Label("Query")
            yield Input(placeholder="Optional Gmail query", id="input-query")
            yield Label("Limit")
            yield Input(placeholder="No limit", id="input-limit", type="integer")
            yield Label("Offset")
            yield Input(placeholder="0", id="input-offset", type="integer")
            yield Label("Batch Size")
            yield Input(placeholder="Default", id="input-batch-size", type="integer")

        yield Static("Progress", classes="section-title")
        with Vertical(classes="progress-area"):
            yield Static("Idle", id="stage-label")
            yield ProgressBar(total=100, show_eta=False, id="progress-bar")

    def get_params(self) -> dict:
        """Collect parameter values from inputs."""
        label = self.query_one("#input-label", Input).value.strip() or None
        query = self.query_one("#input-query", Input).value.strip() or None
        limit_str = self.query_one("#input-limit", Input).value.strip()
        offset_str = self.query_one("#input-offset", Input).value.strip()
        batch_str = self.query_one("#input-batch-size", Input).value.strip()

        return {
            "label_id": label,
            "query": query,
            "limit": int(limit_str) if limit_str else None,
            "offset": int(offset_str) if offset_str else 0,
            "batch_size": int(batch_str) if batch_str else None,
        }

    def set_running(self, running: bool) -> None:
        """Enable/disable operation buttons; Stop button is inverse."""
        for btn in self.query("Button"):
            if btn.id == "btn-stop":
                btn.disabled = not running
            else:
                btn.disabled = running

    def update_progress(self, stage: str, current: int = 0, total: int = 0) -> None:
        """Update the progress display."""
        bar = self.query_one("#progress-bar", ProgressBar)
        label = self.query_one("#stage-label", Static)

        if total > 0:
            bar.update(total=total, progress=current)
            label.update(f"[bold]{stage}[/bold] — {current}/{total}")
        else:
            bar.update(total=100, progress=0)
            label.update(f"[bold]{stage}[/bold]")

    def reset_progress(self) -> None:
        """Reset progress to idle."""
        self.query_one("#progress-bar", ProgressBar).update(total=100, progress=0)
        self.query_one("#stage-label", Static).update("Idle")
