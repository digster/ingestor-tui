"""Unit tests for PresetStore — JSON-based label preset persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestor_tui.preset_store import PresetStore


@pytest.fixture
def store(tmp_path: Path) -> PresetStore:
    return PresetStore(path=tmp_path / "presets.json")


def test_list_empty(store: PresetStore) -> None:
    assert store.list_presets() == {}


def test_save_and_get(store: PresetStore) -> None:
    store.save_preset("work", "INBOX, SENT")
    assert store.get_preset("work") == "INBOX, SENT"


def test_list_after_save(store: PresetStore) -> None:
    store.save_preset("a", "INBOX")
    store.save_preset("b", "SENT")
    presets = store.list_presets()
    assert len(presets) == 2
    assert presets["a"] == "INBOX"
    assert presets["b"] == "SENT"


def test_overwrite(store: PresetStore) -> None:
    store.save_preset("work", "INBOX")
    store.save_preset("work", "INBOX, SENT, TRASH")
    assert store.get_preset("work") == "INBOX, SENT, TRASH"
    assert len(store.list_presets()) == 1


def test_delete(store: PresetStore) -> None:
    store.save_preset("work", "INBOX")
    store.delete_preset("work")
    assert store.get_preset("work") is None
    assert store.list_presets() == {}


def test_delete_missing(store: PresetStore) -> None:
    """Deleting a non-existent preset should not raise."""
    store.delete_preset("nope")


def test_get_missing(store: PresetStore) -> None:
    assert store.get_preset("nope") is None


def test_auto_creates_directory(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "presets.json"
    store = PresetStore(path=nested)
    store.save_preset("x", "INBOX")
    assert nested.exists()
    assert store.get_preset("x") == "INBOX"
