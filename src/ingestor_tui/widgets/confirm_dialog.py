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
    ConfirmDialog.destructive #confirm-container {
        width: 60;
        border: thick $error;
    }
    """

    def __init__(
        self,
        message: str,
        *,
        destructive: bool = False,
        confirm_label: str = "Yes",
    ) -> None:
        """Build the dialog.

        Args:
            message: Rich-markup body text.
            destructive: For irreversible actions. Styles the dialog in the
                error colour and focuses Cancel, so a reflexive Enter backs out
                instead of deleting.
            confirm_label: Text of the confirm button. Naming the action
                ("Prune") reads better than a generic "Yes" when the message
                is long enough that the question scrolls out of mind.
        """
        super().__init__(classes="destructive" if destructive else None)
        self._message = message
        self._destructive = destructive
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-container"):
            yield Static(self._message, id="confirm-message")
            with Horizontal(classes="confirm-buttons"):
                yield Button(
                    self._confirm_label,
                    id="btn-confirm-yes",
                    variant="error" if self._destructive else "primary",
                )
                yield Button("Cancel", id="btn-confirm-cancel", variant="default")

    def on_mount(self) -> None:
        if self._destructive:
            self.query_one("#btn-confirm-cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)
