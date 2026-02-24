"""Reusable confirmation dialog as a Textual ModalScreen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmDialog(ModalScreen[bool]):
    """Modal confirmation dialog that dismisses with True (Yes) or False (Cancel)."""

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
    }
    ConfirmDialog #confirm-container {
        width: 50;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    ConfirmDialog #confirm-message {
        width: 1fr;
        margin: 1 0;
    }
    ConfirmDialog .confirm-buttons {
        height: auto;
        align: center middle;
        margin: 1 0 0 0;
    }
    ConfirmDialog .confirm-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-container"):
            yield Static(self._message, id="confirm-message")
            with Horizontal(classes="confirm-buttons"):
                yield Button("Yes", id="btn-confirm-yes", variant="primary")
                yield Button("Cancel", id="btn-confirm-cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)
