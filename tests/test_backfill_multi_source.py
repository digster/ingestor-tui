"""Tests for labels fed by more than one archive.

A newsletter can outlive a single archive URL — an author rebrands, moves
platform, or changes subdomain, and the back catalogue does not reliably
follow. These cover the schema that expresses that, the listing pass that
aggregates across archives, and the per-source attribution that keeps each
era's articles credited to the address they actually came from.

The live example is ``Neel Chhabra``: "Neel's Newsletter"
(neelchhabra.substack.com) rebranded to "Resight" (resight.substack.com) and
the 23-post back catalogue stayed behind.
"""

from __future__ import annotations

import json

import pytest

from ingestor_tui.backfill.listing import ListingError, read_listing
from ingestor_tui.backfill.mappings import BackfillMapping, MappingError


class FakeFetcher:
    """Serves canned JSON per URL; unknown URLs raise, as a real 404 would."""

    def __init__(self, responses: dict[str, object]) -> None:
        self._responses = responses
        self.requested: list[str] = []

    def get_text(self, url: str) -> str:
        self.requested.append(url)
        if url not in self._responses:
            raise ListingError(f"no such archive page: {url}")
        return json.dumps(self._responses[url])

    def get_json(self, url: str) -> object:
        return json.loads(self.get_text(url))


def _source(host: str, *, name: str = "", sender: str = "", notes: str = "") -> dict:
    """A single-page JSON archive source for ``host``."""
    entry = {
        "archive_url": f"https://{host}/archive",
        "listing": {
            "mode": "json",
            "url_template": f"https://{host}/api?offset={{offset}}&limit={{limit}}",
            "pagination": {"type": "offset", "page_size": 50, "max_pages": 2},
            "items_path": "",
            "fields": {"url": "canonical_url", "title": "title", "date": "post_date"},
        },
        "article": {"content_selector": f"div.{host.split('.')[0]}"},
    }
    if name:
        entry["name"] = name
    if sender:
        entry["sender"] = sender
    if notes:
        entry["notes"] = notes
    return entry


def _mapping(*sources: dict, sender: str = "Fallback <fallback@x.test>") -> BackfillMapping:
    return BackfillMapping.from_dict(
        "Test", {"label_id": "Label_1", "sender": sender, "sources": list(sources)}
    )


def _posts(host: str, *titles: str) -> list[dict]:
    return [
        {
            "canonical_url": f"https://{host}/p/{t.lower().replace(' ', '-')}",
            "title": t,
            "post_date": "2026-01-01T00:00:00Z",
        }
        for t in titles
    ]


def _page(host: str, *titles: str) -> dict:
    """Responses for a single-page archive: page 1 has posts, page 2 is empty."""
    return {
        f"https://{host}/api?offset=0&limit=50": _posts(host, *titles),
        f"https://{host}/api?offset=50&limit=50": [],
    }


# --- schema -----------------------------------------------------------------


def test_sources_list_parses_into_multiple_archives() -> None:
    mapping = _mapping(_source("new.test"), _source("old.test"))

    assert len(mapping.sources) == 2
    assert mapping.is_multi_source
    assert [s.archive_url for s in mapping.sources] == [
        "https://new.test/archive",
        "https://old.test/archive",
    ]


def test_legacy_inline_shape_becomes_one_source() -> None:
    """The single-archive shorthand must keep working untouched."""
    mapping = BackfillMapping.from_dict(
        "Legacy",
        {
            "archive_url": "https://x.test/archive",
            "sender": "A <a@x.test>",
            "listing": {
                "mode": "json",
                "url_template": "https://x.test/api?offset={offset}&limit={limit}",
                "pagination": {"type": "offset"},
                "fields": {"url": "canonical_url", "title": "title"},
            },
            "article": {"content_selector": "div.body"},
            "notes": "mapping-level prose",
        },
    )

    assert len(mapping.sources) == 1
    assert not mapping.is_multi_source
    # Back-compat accessors still read naturally.
    assert mapping.archive_url == "https://x.test/archive"
    assert mapping.listing.mode == "json"
    assert mapping.article.content_selector == "div.body"
    # Mapping-level notes stay at the mapping level rather than being echoed
    # onto the lone source, which would print the same prose twice.
    assert mapping.notes == "mapping-level prose"
    assert mapping.sources[0].notes == ""


def test_both_shapes_at_once_is_rejected() -> None:
    """Ambiguity about which archive is primary must fail loudly."""
    with pytest.raises(MappingError, match="not both"):
        BackfillMapping.from_dict(
            "Test",
            {
                "archive_url": "https://x.test/archive",
                "listing": {
                    "mode": "json",
                    "url_template": "https://x.test/api?offset={offset}",
                    "pagination": {"type": "offset"},
                    "fields": {"url": "u", "title": "t"},
                },
                "sources": [_source("y.test")],
            },
        )


def test_empty_sources_list_is_rejected() -> None:
    with pytest.raises(MappingError, match="non-empty list"):
        BackfillMapping.from_dict("Test", {"sources": []})


def test_duplicate_archive_url_is_rejected() -> None:
    """Listing the same archive twice would double every article."""
    with pytest.raises(MappingError, match="duplicate archive_url"):
        _mapping(_source("same.test"), _source("same.test"))


def test_source_errors_name_their_index() -> None:
    with pytest.raises(MappingError, match=r"source\[1\]"):
        BackfillMapping.from_dict(
            "Test", {"sources": [_source("a.test"), {"archive_url": "https://b.test/a"}]}
        )


def test_per_source_sender_overrides_the_mapping_sender() -> None:
    mapping = _mapping(
        _source("new.test", sender="New <new@x.test>"),
        _source("old.test"),
        sender="Fallback <fallback@x.test>",
    )

    assert mapping.sources[0].sender == "New <new@x.test>"
    # A source that names no sender inherits the mapping's, resolved at parse
    # time so no consumer has to know that blank means "inherit".
    assert mapping.sources[1].sender == "Fallback <fallback@x.test>"


def test_source_at_clamps_out_of_range_indexes() -> None:
    """A stale source_index must fall back, not crash a run mid-flight."""
    mapping = _mapping(_source("new.test"), _source("old.test"))

    assert mapping.source_at(1).archive_url == "https://old.test/archive"
    assert mapping.source_at(9) is mapping.sources[0]
    assert mapping.source_at(-1) is mapping.sources[0]


def test_display_name_falls_back_to_the_host() -> None:
    mapping = _mapping(_source("new.test", name="Resight"), _source("old.test"))

    assert mapping.sources[0].display_name == "Resight"
    assert mapping.sources[1].display_name == "old.test"


# --- listing ----------------------------------------------------------------


def test_listing_concatenates_sources_in_order() -> None:
    mapping = _mapping(_source("new.test"), _source("old.test"))
    fetcher = FakeFetcher({**_page("new.test", "New One"), **_page("old.test", "Old One")})

    refs = read_listing(mapping, fetcher)

    assert [r.title for r in refs] == ["New One", "Old One"]


def test_listing_tags_each_ref_with_its_source() -> None:
    """Without this the runner would extract old articles with new selectors."""
    mapping = _mapping(_source("new.test"), _source("old.test"))
    fetcher = FakeFetcher({**_page("new.test", "New One"), **_page("old.test", "Old One")})

    refs = read_listing(mapping, fetcher)

    assert [r.source_index for r in refs] == [0, 1]
    assert mapping.source_at(refs[1].source_index).article.content_selector == "div.old"


def test_listing_dedupes_the_same_url_across_sources() -> None:
    mapping = _mapping(_source("a.test"), _source("b.test"))
    shared = {
        "canonical_url": "https://a.test/p/shared",
        "title": "Shared",
        "post_date": "2026-01-01T00:00:00Z",
    }
    fetcher = FakeFetcher(
        {
            "https://a.test/api?offset=0&limit=50": [shared],
            "https://a.test/api?offset=50&limit=50": [],
            "https://b.test/api?offset=0&limit=50": [shared],
            "https://b.test/api?offset=50&limit=50": [],
        }
    )

    refs = read_listing(mapping, fetcher)

    assert len(refs) == 1


def test_listing_dedupes_the_same_title_at_different_urls() -> None:
    """The migration case: both archives carry the post at different URLs.

    URL dedup alone would mint two IDs and write the article twice.
    """
    mapping = _mapping(_source("new.test"), _source("old.test"))
    fetcher = FakeFetcher(
        {
            **_page("new.test", "Imported Post"),
            **_page("old.test", "Imported Post!", "Only Old"),
        }
    )

    refs = read_listing(mapping, fetcher)

    assert [r.title for r in refs] == ["Imported Post", "Only Old"]
    # The surviving copy is the primary archive's.
    assert refs[0].source_index == 0


def test_a_fully_overlapping_source_does_not_stop_the_walk_early() -> None:
    """Discarded duplicates still count as progress.

    Treating an all-duplicate page as "no new URLs" would end the archive walk
    on its first page and hide everything after it.
    """
    mapping = _mapping(_source("new.test"), _source("old.test"))
    fetcher = FakeFetcher(
        {
            **_page("new.test", "Shared A", "Shared B"),
            "https://old.test/api?offset=0&limit=50": _posts("old.test", "Shared A", "Shared B"),
            "https://old.test/api?offset=50&limit=50": _posts("old.test", "Genuinely Old"),
        }
    )

    refs = read_listing(mapping, fetcher)

    assert "Genuinely Old" in [r.title for r in refs]


def test_limit_spans_sources_rather_than_applying_per_archive() -> None:
    mapping = _mapping(_source("new.test"), _source("old.test"))
    fetcher = FakeFetcher(
        {**_page("new.test", "A", "B"), **_page("old.test", "C", "D")}
    )

    refs = read_listing(mapping, fetcher, limit=3)

    assert [r.title for r in refs] == ["A", "B", "C"]


def test_a_dead_source_does_not_lose_the_others() -> None:
    """An old publication's URL going away is exactly what this must survive."""
    mapping = _mapping(_source("gone.test"), _source("live.test"))
    fetcher = FakeFetcher(_page("live.test", "Still Here"))

    refs = read_listing(mapping, fetcher)

    assert [r.title for r in refs] == ["Still Here"]


def test_every_source_failing_still_raises() -> None:
    mapping = _mapping(_source("gone.test"), _source("alsogone.test"))
    fetcher = FakeFetcher({})

    with pytest.raises(ListingError, match="any of its 2 archives"):
        read_listing(mapping, fetcher)


def test_a_single_source_failure_still_propagates() -> None:
    """Only multi-source mappings tolerate a dead archive."""
    mapping = _mapping(_source("gone.test"))
    fetcher = FakeFetcher({})

    with pytest.raises(ListingError):
        read_listing(mapping, fetcher)
