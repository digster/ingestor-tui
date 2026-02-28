"""Modal dialog for entering a preset name."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class PresetNameDialog(ModalScreen[str]):
    """Ask the user for a preset name. Dismisses with the name or empty string on cancel."""

    DEFAULT_CSS = """
    PresetNameDialog {
        align: center middle;
    }
    PresetNameDialog #preset-name-container {
        width: 50;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    PresetNameDialog #preset-name-label {
        width: 1fr;
        margin: 1 0;
    }
    PresetNameDialog #preset-name-input {
        margin: 0 0 1 0;
    }
    PresetNameDialog .preset-name-buttons {
        height: auto;
        align: center middle;
        margin: 1 0 0 0;
    }
    PresetNameDialog .preset-name-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, title: str = "Save Preset") -> None:
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="preset-name-container"):
            yield Static(self._title, id="preset-name-label")
            yield Input(placeholder="Preset name", id="preset-name-input")
            with Horizontal(classes="preset-name-buttons"):
                yield Button("Save", id="btn-preset-save", variant="primary")
                yield Button("Cancel", id="btn-preset-cancel", variant="default")

    def on_mount(self) -> None:
        self.query_one("#preset-name-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-preset-save":
            name = self.query_one("#preset-name-input", Input).value.strip()
            self.dismiss(name)
        else:
            self.dismiss("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        self.dismiss(name)
