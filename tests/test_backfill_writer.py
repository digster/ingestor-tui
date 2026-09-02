"""Tests for output parity — backfilled files must look like ingested ones."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from ingestor_tui.backfill.mappings import BackfillMapping
from ingestor_tui.backfill.models import ExtractedArticle
from ingestor_tui.backfill.writer import BackfillWriter, _inject_front_matter

ARTICLE_ID = "web-0123456789abcdef"


@pytest.fixture
def mapping() -> BackfillMapping:
    return BackfillMapping.from_dict(
        "Example Newsletter",
        {
            "label_id": "Label_123",
            "archive_url": "https://x.test/archive",
            "sender": 'Author "Ace" <author@x.test>',
            "listing": {
                "mode": "json",
                "url_template": "https://x.test/api?offset={offset}&limit={limit}",
                "pagination": {"type": "offset"},
                "fields": {"url": "url", "title": "title"},
            },
            "article": {"content_selector": "div.content"},
        },
    )


@pytest.fixture
def article() -> ExtractedArticle:
    return ExtractedArticle(
        url="https://x.test/p/hello-world",
        title="Hello, World!",
        published_at=datetime(2026, 8, 27, 7, 21, 46, tzinfo=UTC),
        content_html="<html><body><article><p>Body text here.</p></article></body></html>",
    )


@pytest.fixture
def writer(tmp_path: Path) -> BackfillWriter:
    return BackfillWriter(tmp_path / "markdown", tmp_path / "raw")


def front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    block = text.split("---", 2)[1]
    return yaml.safe_load(block)


# --- file layout ---


def test_writes_markdown_and_raw_html(writer, article, mapping, tmp_path) -> None:
    result = writer.write(ARTICLE_ID, article, mapping)

    assert result.markdown_path.name == f"hello-world_{ARTICLE_ID}.md"
    assert result.raw_html_path.name == f"{ARTICLE_ID}.html"
    assert result.markdown_path.exists()
    assert result.raw_html_path.exists()


def test_writes_no_txt_file(writer, article, mapping, tmp_path) -> None:
    """newsletters-web never uses .txt as a body, so none is written."""
    writer.write(ARTICLE_ID, article, mapping)
    assert list((tmp_path / "raw").glob("*.txt")) == []


def test_id_survives_downstream_filename_parsing(writer, article, mapping) -> None:
    """ingestor-tools recovers the ID with rsplit("_", 1) — no underscores allowed."""
    result = writer.write(ARTICLE_ID, article, mapping)
    assert result.markdown_path.stem.rsplit("_", 1)[-1] == ARTICLE_ID
    assert "_" not in ARTICLE_ID


# --- front matter parity ---


def test_front_matter_has_the_ingested_keys(writer, article, mapping) -> None:
    meta = front_matter(writer.write(ARTICLE_ID, article, mapping).markdown_path)

    assert meta["id"] == ARTICLE_ID
    assert meta["subject"] == "Hello, World!"
    assert meta["from"] == 'Author "Ace" <author@x.test>'
    assert meta["to"] == ""
    assert meta["labels"] == ["Example Newsletter"]
    assert meta["label_ids"] == ["Label_123"]


def test_front_matter_carries_provenance(writer, article, mapping) -> None:
    meta = front_matter(writer.write(ARTICLE_ID, article, mapping).markdown_path)
    assert meta["source_url"] == "https://x.test/p/hello-world"
    assert meta["origin"] == "backfill"


def test_id_stays_a_string_after_yaml_load(writer, article, mapping) -> None:
    """Gmail IDs are quoted so all-digit ones don't load as ints; so are ours."""
    meta = front_matter(writer.write(ARTICLE_ID, article, mapping).markdown_path)
    assert isinstance(meta["id"], str)


def test_date_is_utc_in_the_ingested_format(writer, article, mapping) -> None:
    text = writer.write(ARTICLE_ID, article, mapping).markdown_path.read_text()
    assert "date: 2026-08-27 07:21:46" in text


def test_naive_and_aware_dates_agree(writer, mapping, tmp_path) -> None:
    """A tz-aware source date is converted, not truncated."""
    from datetime import timedelta, timezone

    aware = ExtractedArticle(
        url="https://x.test/p/tz",
        title="TZ",
        published_at=datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=5))),
        content_html="<html><body><p>x</p></body></html>",
    )
    text = writer.write("web-tz", aware, mapping).markdown_path.read_text()
    assert "date: 2026-08-27 12:00:00" in text


def test_backslash_in_sender_survives_yaml(writer, article, tmp_path) -> None:
    """Real Gmail senders contain backslashes; escaping order has bitten before."""
    mapping = BackfillMapping.from_dict(
        "Example Newsletter",
        {
            "archive_url": "https://x.test/archive",
            "sender": '"\\"Mr. and Mrs. Psmith\'s Bookshelf\\"" <a@x.test>',
            "listing": {
                "mode": "json",
                "url_template": "https://x.test/api?offset={offset}&limit={limit}",
                "pagination": {"type": "offset"},
                "fields": {"url": "url", "title": "title"},
            },
        },
    )
    meta = front_matter(writer.write(ARTICLE_ID, article, mapping).markdown_path)
    assert meta["from"] == '"\\"Mr. and Mrs. Psmith\'s Bookshelf\\"" <a@x.test>'


def test_body_follows_front_matter(writer, article, mapping) -> None:
    text = writer.write(ARTICLE_ID, article, mapping).markdown_path.read_text()
    assert "Body text here." in text.split("---", 2)[2]


def test_no_label_id_omits_the_key(writer, article) -> None:
    """Matches the converter, which only emits label_ids when non-empty."""
    mapping = BackfillMapping.from_dict(
        "Example Newsletter",
        {
            "archive_url": "https://x.test/archive",
            "listing": {
                "mode": "json",
                "url_template": "https://x.test/api?offset={offset}&limit={limit}",
                "pagination": {"type": "offset"},
                "fields": {"url": "url", "title": "title"},
            },
        },
    )
    meta = front_matter(writer.write(ARTICLE_ID, article, mapping).markdown_path)
    assert "label_ids" not in meta
    assert meta["labels"] == ["Example Newsletter"]


def test_unconvertible_body_writes_nothing(writer, mapping, tmp_path) -> None:
    """A conversion failure must not leave a half-written pair on disk."""
    from gmail_ingestor.core.exceptions import ConversionError

    empty = ExtractedArticle(
        url="https://x.test/p/empty",
        title="Empty",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_html="",
    )
    with pytest.raises(ConversionError):
        writer.write("web-empty", empty, mapping)

    assert list((tmp_path / "markdown").glob("*.md")) == []
    assert list((tmp_path / "raw").glob("*.html")) == []


# --- front-matter injection ---


def test_injection_is_a_no_op_without_front_matter() -> None:
    assert _inject_front_matter("just a body", {"origin": "backfill"}) == "just a body"


def test_injection_is_a_no_op_when_unclosed() -> None:
    text = "---\nid: \"a\"\nno closing delimiter"
    assert _inject_front_matter(text, {"origin": "backfill"}) == text


def test_injection_places_keys_inside_the_block() -> None:
    text = '---\nid: "a"\n---\nbody\n'
    result = _inject_front_matter(text, {"origin": "backfill"})
    assert result == '---\nid: "a"\norigin: "backfill"\n---\nbody\n'
