"""Persistent storage for label presets as a JSON file."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "ingestor-tui" / "label_presets.json"


class PresetStore:
    """Read/write named label presets from a JSON file."""

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self._path = path

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())

    def _write(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2) + "\n")

    def list_presets(self) -> dict[str, str]:
        """Return all presets as ``{name: labels}``."""
        return self._read()

    def save_preset(self, name: str, labels: str) -> None:
        """Save or overwrite a preset."""
        data = self._read()
        data[name] = labels
        self._write(data)

    def delete_preset(self, name: str) -> None:
        """Delete a preset by name (no-op if missing)."""
        data = self._read()
        data.pop(name, None)
        self._write(data)

    def get_preset(self, name: str) -> str | None:
        """Return the labels string for a preset, or ``None``."""
        return self._read().get(name)
