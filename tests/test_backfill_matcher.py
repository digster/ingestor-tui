"""Tests for gap detection — the rules that decide what we already hold."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ingestor_tui.backfill.identity import article_id_for, canonicalize_url
from ingestor_tui.backfill.matcher import (
    CorpusIndex,
    HeldMessage,
    classify,
    index_from_markdown,
    title_key,
    url_slug,
)
from ingestor_tui.backfill.models import ArticleRef


def ref(title: str, slug: str = "", date: str = "2026-05-01") -> ArticleRef:
    slug = slug or title.lower().replace(" ", "-")
    return ArticleRef(
        url=f"https://x.test/p/{slug}",
        title=title,
        published_at=datetime.fromisoformat(date).replace(tzinfo=UTC),
    )


@pytest.fixture
def corpus() -> CorpusIndex:
    return CorpusIndex(
        [
            HeldMessage("aaa", "The Costco theory of the internet"),
            HeldMessage("bbb", "How to Build a Roadmap for the Life You Actually Want"),
            HeldMessage("ccc", "Kant’s Adult Is Extinct."),
            HeldMessage("ddd", "Why I can't stand the word “driven”"),
        ]
    )


# --- normalisation ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello World", "hello-world"),
        ("Kant’s Adult Is Extinct.", "kants-adult-is-extinct"),
        ("Kant's Adult Is Extinct.", "kants-adult-is-extinct"),
        ("Re: Weekly Digest", "weekly-digest"),
        ("FWD: Re: Weekly Digest", "weekly-digest"),
        ("Why I can't stand the word “driven”", "why-i-cant-stand-the-word-driven"),
        ("", ""),
    ],
)
def test_title_key_normalisation(text: str, expected: str) -> None:
    assert title_key(text) == expected


def test_unicode_dashes_do_not_glue_words() -> None:
    """ASCII-folding deletes em-dashes; without substitution words would merge."""
    assert title_key("state—of—the—art") == "state-of-the-art"
    assert title_key("state–of–the–art") == "state-of-the-art"


def test_title_key_is_not_truncated_at_writer_length() -> None:
    """Prefix matching needs the full slug, not the writer's 50-char filename cap."""
    long_title = "word " * 30
    assert len(title_key(long_title)) > 50


def test_url_slug_ignores_query_and_trailing_slash() -> None:
    assert url_slug("https://x.test/p/some-post/?utm_source=rss") == "some-post"


# --- matching rules ---


def test_exact_title_match(corpus: CorpusIndex) -> None:
    found = corpus.find(ref("The Costco theory of the internet"))
    assert found is not None
    assert found[0].message_id == "aaa"
    assert "exact" in found[1]


def test_smart_quote_variant_matches(corpus: CorpusIndex) -> None:
    assert corpus.find(ref("Kant's Adult Is Extinct.")) is not None


def test_truncated_url_slug_matches_full_subject(corpus: CorpusIndex) -> None:
    """Substack truncates slugs to ~6 words; the title is absent from the feed."""
    entry = ArticleRef(url="https://x.test/p/how-to-build-a-roadmap-for-the-life", title="")
    found = corpus.find(entry)
    assert found is not None
    assert found[0].message_id == "bbb"
    assert "prefix" in found[1]


def test_short_slug_does_not_prefix_match(corpus: CorpusIndex) -> None:
    """A slug below the minimum length is too weak to be evidence."""
    assert corpus.find(ArticleRef(url="https://x.test/p/how-to", title="")) is None


def test_small_rewording_matches_fuzzily(corpus: CorpusIndex) -> None:
    found = corpus.find(ref("The Costco theory of the Internet."))
    assert found is not None
    assert found[0].message_id == "aaa"


def test_unrelated_title_does_not_match(corpus: CorpusIndex) -> None:
    assert corpus.find(ref("An entirely different essay about gardening")) is None


def test_empty_corpus_matches_nothing() -> None:
    assert CorpusIndex([]).find(ref("Anything")) is None


def test_duplicate_subjects_index_once() -> None:
    index = CorpusIndex([HeldMessage("a", "Weekly"), HeldMessage("b", "Weekly")])
    assert len(index.keys) == 1
    assert len(index) == 2


# --- classify ---


def test_classify_splits_held_and_missing(corpus: CorpusIndex) -> None:
    entries = classify([ref("The Costco theory of the internet"), ref("A brand new post")], corpus)
    assert [e.status for e in entries] == ["have", "discovered"]
    assert entries[1].is_missing


def test_classify_assigns_stable_ids(corpus: CorpusIndex) -> None:
    entry = classify([ref("A brand new post")], corpus)[0]
    assert entry.article_id == article_id_for(entry.ref.url)
    assert entry.article_id.startswith("web-")


def test_known_urls_short_circuit_title_rules(corpus: CorpusIndex) -> None:
    """A completed backfill is never re-scraped, even if the headline changed."""
    entry = ref("Some post the publisher later retitled")
    known = {canonicalize_url(entry.url)}
    result = classify([entry], corpus, known_urls=known)[0]
    assert result.status == "have"
    assert result.match_reason == "already backfilled"


def test_same_article_twice_yields_one_id(corpus: CorpusIndex) -> None:
    """Tracking parameters must not mint a second ID for one article."""
    a = ArticleRef(url="https://x.test/p/post", title="Post")
    b = ArticleRef(url="https://x.test/p/post?utm_source=feed", title="Post")
    ids = {e.article_id for e in classify([a, b], corpus)}
    assert len(ids) == 1


def test_untitled_article_still_gets_a_unique_id(corpus: CorpusIndex) -> None:
    """A title that slugifies to nothing must not break ID assignment."""
    entries = classify(
        [ArticleRef(url="https://x.test/p/a", title="!!!"),
         ArticleRef(url="https://x.test/p/b", title="???")],
        corpus,
    )
    assert len({e.article_id for e in entries}) == 2


# --- markdown fallback ---


def _write_md(directory: Path, name: str, subject: str, labels: list[str]) -> None:
    label_list = ", ".join(f'"{label}"' for label in labels)
    (directory / name).write_text(
        f'---\nid: "abc"\nsubject: "{subject}"\nfrom: ""\nto: ""\n'
        f"date: 2026-05-01 10:00:00\nlabels: [{label_list}]\n---\nbody\n",
        encoding="utf-8",
    )


def test_markdown_fallback_indexes_matching_label(tmp_path: Path) -> None:
    _write_md(tmp_path, "one_aaa.md", "A Held Post", ["INBOX", "Example"])
    _write_md(tmp_path, "two_bbb.md", "Another Newsletter", ["Other"])

    index = index_from_markdown(tmp_path, "Example")
    assert len(index) == 1
    assert index.find(ref("A Held Post")) is not None


def test_markdown_fallback_recovers_message_id(tmp_path: Path) -> None:
    _write_md(tmp_path, "slug-here_19e6c33bbf2f63b6.md", "A Held Post", ["Example"])
    found = index_from_markdown(tmp_path, "Example").find(ref("A Held Post"))
    assert found is not None
    assert found[0].message_id == "19e6c33bbf2f63b6"


def test_markdown_fallback_handles_escaped_subject(tmp_path: Path) -> None:
    """Front matter escapes quotes and backslashes; the fallback must unescape."""
    (tmp_path / "x_aaa.md").write_text(
        '---\nsubject: "Mr. and Mrs. Psmith\\"s Bookshelf"\nlabels: ["Example"]\n---\nbody\n',
        encoding="utf-8",
    )
    index = index_from_markdown(tmp_path, "Example")
    assert index.find(ref('Mr. and Mrs. Psmith"s Bookshelf')) is not None


def test_markdown_fallback_skips_files_without_front_matter(tmp_path: Path) -> None:
    (tmp_path / "bad_aaa.md").write_text("no front matter here\n", encoding="utf-8")
    assert len(index_from_markdown(tmp_path, "Example")) == 0


def test_markdown_fallback_on_missing_directory(tmp_path: Path) -> None:
    assert len(index_from_markdown(tmp_path / "nope", "Example")) == 0
