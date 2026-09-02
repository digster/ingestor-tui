"""Tests for the backfill mapping file — schema validation and persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestor_tui.backfill.mappings import (
    ArticleConfig,
    BackfillMapping,
    ListingConfig,
    MappingError,
    MappingStore,
)


def _json_entry(**overrides) -> dict:
    entry = {
        "label_id": "Label_123",
        "archive_url": "https://example.com/archive",
        "sender": "Author <author@example.com>",
        "listing": {
            "mode": "json",
            "url_template": "https://example.com/api?offset={offset}&limit={limit}",
            "pagination": {"type": "offset", "page_size": 50},
            "items_path": "",
            "fields": {"url": "canonical_url", "title": "title", "date": "post_date"},
        },
        "article": {"content_selector": "div.content"},
    }
    entry.update(overrides)
    return entry


def _html_entry(**listing_overrides) -> dict:
    listing = {
        "mode": "html",
        "url_template": "https://example.com/archive/page/{page}",
        "pagination": {"type": "page", "start": 1},
        "item_selector": "article.post",
        "fields": {
            "url": {"selector": "a.title", "attr": "href"},
            "title": {"selector": "a.title", "attr": "text"},
        },
    }
    listing.update(listing_overrides)
    return {"archive_url": "https://example.com/archive", "listing": listing}


@pytest.fixture
def store(tmp_path: Path) -> MappingStore:
    return MappingStore(tmp_path / "backfill_mappings.json")


def test_missing_file_is_empty(store: MappingStore) -> None:
    assert store.list_mappings() == {}


def test_round_trip(store: MappingStore) -> None:
    store.save("Example", _json_entry())
    mappings = store.list_mappings()
    assert list(mappings) == ["Example"]

    mapping = mappings["Example"]
    assert isinstance(mapping, BackfillMapping)
    assert mapping.label_id == "Label_123"
    assert mapping.listing.mode == "json"
    assert mapping.listing.pagination.page_size == 50
    assert mapping.article.content_selector == "div.content"


def test_save_overwrites_and_delete_removes(store: MappingStore) -> None:
    store.save("Example", _json_entry())
    store.save("Example", _json_entry(sender="New <new@example.com>"))
    assert store.list_mappings()["Example"].sender == "New <new@example.com>"

    store.delete("Example")
    assert store.list_mappings() == {}
    store.delete("Example")  # deleting twice is a no-op


def test_get_names_available_labels(store: MappingStore) -> None:
    store.save("Alpha", _json_entry())
    with pytest.raises(MappingError, match="Alpha"):
        store.get("Missing")


def test_invalid_json_is_reported(store: MappingStore) -> None:
    store.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(MappingError, match="not valid JSON"):
        store.list_mappings()


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (_json_entry(archive_url=""), "archive_url is required"),
        (_json_entry(archive_url="ftp://example.com"), "must be http"),
        ({"archive_url": "https://example.com"}, "listing section is required"),
    ],
)
def test_entry_level_validation(store: MappingStore, entry: dict, message: str) -> None:
    with pytest.raises(MappingError, match=message):
        store.save("Bad", entry)


def test_unknown_mode_rejected() -> None:
    with pytest.raises(MappingError, match="listing.mode"):
        ListingConfig.from_dict(
            {"mode": "carrier-pigeon", "url_template": "https://x/", "fields": {}}
        )


def test_missing_required_fields_rejected() -> None:
    with pytest.raises(MappingError, match="fields is missing"):
        ListingConfig.from_dict(
            {"mode": "json", "url_template": "https://x/", "fields": {"url": "u"}}
        )


def test_html_mode_requires_item_selector() -> None:
    entry = _html_entry(item_selector="")
    with pytest.raises(MappingError, match="item_selector is required"):
        BackfillMapping.from_dict("Bad", entry)


def test_offset_pagination_requires_placeholder() -> None:
    """A template without {offset} would refetch page 1 forever.

    This is the exact Substack failure mode the feature exists to avoid, so it
    is rejected at authoring time rather than discovered after a partial run.
    """
    entry = _json_entry()
    entry["listing"]["url_template"] = "https://example.com/api?limit={limit}"
    with pytest.raises(MappingError, match=r"requires \{offset\}"):
        BackfillMapping.from_dict("Bad", entry)


def test_page_pagination_requires_placeholder() -> None:
    entry = _html_entry(url_template="https://example.com/archive")
    with pytest.raises(MappingError, match=r"requires \{page\}"):
        BackfillMapping.from_dict("Bad", entry)


def test_none_pagination_needs_no_placeholder() -> None:
    entry = _html_entry(
        url_template="https://example.com/archive", pagination={"type": "none"}
    )
    mapping = BackfillMapping.from_dict("Fine", entry)
    assert mapping.listing.pagination.type == "none"


def test_save_rejects_invalid_before_writing(store: MappingStore) -> None:
    """A rejected save must not leave a partial file behind."""
    with pytest.raises(MappingError):
        store.save("Bad", {"archive_url": "https://example.com"})
    assert not store.path.exists()


def test_shipped_mapping_file_is_valid() -> None:
    """The repo's own backfill_mappings.json must always load."""
    store = MappingStore()
    if not store.path.exists():
        pytest.skip("no mapping file in the repo")
    mappings = store.list_mappings()
    assert mappings, "mapping file exists but contains no entries"
    for name, mapping in mappings.items():
        assert mapping.archive_url.startswith("https://"), name
        assert mapping.listing.fields.get("url"), name


def test_file_is_written_as_readable_json(store: MappingStore) -> None:
    store.save("Example", _json_entry())
    data = json.loads(store.path.read_text())
    assert data["version"] == 1
    assert "Example" in data["mappings"]
    assert store.path.read_text().endswith("\n")


# --- article.strip_selectors ---


def test_strip_selectors_default_to_empty_meaning_the_extractor_default() -> None:
    """Resolved in extractor.py, not here — mappings.py must not import back
    from a module that already imports it."""
    assert ArticleConfig.from_dict({}).strip_selectors == ()


def test_strip_selectors_are_parsed_into_a_tuple() -> None:
    config = ArticleConfig.from_dict({"strip_selectors": ["script", "aside"]})
    assert config.strip_selectors == ("script", "aside")


def test_a_bare_string_of_selectors_is_rejected() -> None:
    """"button, svg" would otherwise iterate character by character."""
    with pytest.raises(MappingError, match="strip_selectors"):
        ArticleConfig.from_dict({"strip_selectors": "button, svg"})
