"""End-to-end tests for BackfillRunner, with the network stubbed out."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from gmail_ingestor.config.settings import GmailIngestorSettings

from ingestor_tui.backfill import runner as runner_module
from ingestor_tui.backfill.mappings import MappingStore
from ingestor_tui.backfill.runner import BackfillRunner
from ingestor_tui.backfill.store import BackfillTracker

ARCHIVE_API = "https://x.test/api?offset={offset}&limit={limit}"

ARTICLE_PAGE = """
<html><head><meta property="article:published_time" content="2026-04-05T09:00:00Z"></head>
<body><div class="content"><p>The body of the article.</p></div></body></html>
"""


class FakeFetcher:
    """Serves the archive JSON and every article page from one dict."""

    def __init__(self, pages: dict[str, str], fail: set[str] | None = None) -> None:
        self.pages = pages
        self.fail = fail or set()
        self.requested: list[str] = []

    def get_text(self, url: str) -> str:
        self.requested.append(url)
        if url in self.fail:
            raise RuntimeError(f"HTTP 404 for {url}")
        return self.pages.get(url, ARTICLE_PAGE)

    def get_json(self, url: str) -> object:
        return json.loads(self.get_text(url))

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def archive_json(*titles: str) -> str:
    return json.dumps(
        [
            {
                "canonical_url": f"https://x.test/p/{t.lower().replace(' ', '-')}",
                "title": t,
                "post_date": "2026-04-05T09:00:00.000Z",
            }
            for t in titles
        ]
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A gmail-ingestor-shaped project directory with a seeded database."""
    (tmp_path / "data").mkdir()
    (tmp_path / "output" / "markdown").mkdir(parents=True)
    (tmp_path / "output" / "raw").mkdir(parents=True)

    conn = sqlite3.connect(tmp_path / "data" / "gmail_ingestor.db")
    conn.executescript("""
        CREATE TABLE messages (
            message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
            label_id TEXT DEFAULT '', status TEXT DEFAULT 'converted',
            subject TEXT DEFAULT '', sender TEXT DEFAULT '', date TEXT DEFAULT '',
            raw_text_path TEXT DEFAULT '', raw_html_path TEXT DEFAULT '',
            markdown_path TEXT DEFAULT '', error_message TEXT DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE labels (
            label_id TEXT PRIMARY KEY, label_name TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE message_labels (
            message_id TEXT NOT NULL, label_id TEXT NOT NULL,
            PRIMARY KEY (message_id, label_id));
    """)
    now = datetime.now(UTC).isoformat()
    conn.execute("INSERT INTO labels VALUES ('Label_1', 'Example', ?)", (now,))
    conn.execute(
        "INSERT INTO messages (message_id, thread_id, subject, created_at, updated_at) "
        "VALUES ('aaa', 't', 'Already Held', ?, ?)", (now, now))
    conn.execute("INSERT INTO message_labels VALUES ('aaa', 'Label_1')")
    conn.commit()
    conn.close()

    (tmp_path / ".env").write_text(
        "GMAIL_DATABASE_PATH=data/gmail_ingestor.db\n"
        "GMAIL_OUTPUT_MARKDOWN_DIR=output/markdown\n"
        "GMAIL_OUTPUT_RAW_DIR=output/raw\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def mapping_store(tmp_path: Path) -> MappingStore:
    store = MappingStore(tmp_path / "mappings.json")
    store.save(
        "Example",
        {
            "label_id": "Label_1",
            "archive_url": "https://x.test/archive",
            "sender": "Author <a@x.test>",
            "listing": {
                "mode": "json",
                "url_template": ARCHIVE_API,
                "pagination": {"type": "offset", "page_size": 10, "max_pages": 3},
                "fields": {"url": "canonical_url", "title": "title", "date": "post_date"},
            },
            "article": {"content_selector": "div.content"},
        },
    )
    return store


@pytest.fixture
def stub_fetcher(monkeypatch: pytest.MonkeyPatch):
    """Install a FakeFetcher in place of the real one for a whole run."""
    holder: dict[str, FakeFetcher] = {}

    def install(pages: dict[str, str], fail: set[str] | None = None) -> FakeFetcher:
        fetcher = FakeFetcher(pages, fail)
        holder["f"] = fetcher
        monkeypatch.setattr(runner_module, "Fetcher", lambda **kwargs: fetcher)
        return fetcher

    return install


def make_runner(mapping_store: MappingStore, **kwargs) -> BackfillRunner:
    return BackfillRunner(GmailIngestorSettings(), mapping_store=mapping_store, **kwargs)


ONE_PAGE = {
    "https://x.test/api?offset=0&limit=10": archive_json("Already Held", "A New Post"),
    "https://x.test/api?offset=10&limit=10": "[]",
}


# --- scan ---


def test_scan_separates_held_from_missing(project, mapping_store, stub_fetcher) -> None:
    stub_fetcher(ONE_PAGE)
    entries = make_runner(mapping_store).scan("Example")

    assert len(entries) == 2
    by_title = {e.ref.title: e for e in entries}
    assert by_title["Already Held"].status == "have"
    assert by_title["A New Post"].is_missing


def test_scan_writes_nothing(project, mapping_store, stub_fetcher) -> None:
    stub_fetcher(ONE_PAGE)
    make_runner(mapping_store).scan("Example")

    assert list((project / "output" / "markdown").iterdir()) == []
    assert not (project / "data" / "backfill.db").exists()


def test_scan_reports_progress(project, mapping_store, stub_fetcher) -> None:
    stub_fetcher(ONE_PAGE)
    stages: list[str] = []
    make_runner(mapping_store, on_progress=lambda p: stages.append(p.current_stage)).scan("Example")
    assert stages[0] == "listing"
    assert "matching" in stages
    assert stages[-1] == "complete"


def test_unknown_label_raises(project, mapping_store, stub_fetcher) -> None:
    from ingestor_tui.backfill.mappings import MappingError

    stub_fetcher(ONE_PAGE)
    with pytest.raises(MappingError, match="No backfill mapping"):
        make_runner(mapping_store).scan("Nonexistent")


# --- run ---


def test_run_writes_only_the_missing_article(project, mapping_store, stub_fetcher) -> None:
    stub_fetcher(ONE_PAGE)
    result = make_runner(mapping_store).run("Example")

    assert (result.listed, result.already_held, result.written, result.failed) == (2, 1, 1, 0)
    written = list((project / "output" / "markdown").glob("*.md"))
    assert len(written) == 1
    assert written[0].name.startswith("a-new-post_web-")
    assert len(list((project / "output" / "raw").glob("*.html"))) == 1


def test_dry_run_writes_nothing(project, mapping_store, stub_fetcher) -> None:
    stub_fetcher(ONE_PAGE)
    result = make_runner(mapping_store).run("Example", dry_run=True)

    assert result.dry_run and result.selected == 1 and result.written == 0
    assert list((project / "output" / "markdown").iterdir()) == []
    assert not (project / "data" / "backfill.db").exists()


def test_limit_caps_writes_not_listing(project, mapping_store, stub_fetcher) -> None:
    stub_fetcher({
        "https://x.test/api?offset=0&limit=10": archive_json("New One", "New Two", "New Three"),
        "https://x.test/api?offset=10&limit=10": "[]",
    })
    result = make_runner(mapping_store).run("Example", limit=2)

    assert result.listed == 3
    assert result.selected == 2
    assert result.written == 2


def test_rerun_is_idempotent(project, mapping_store, stub_fetcher) -> None:
    """A completed article is never re-fetched or rewritten."""
    stub_fetcher(ONE_PAGE)
    make_runner(mapping_store).run("Example")

    fetcher = stub_fetcher(ONE_PAGE)
    second = make_runner(mapping_store).run("Example")

    assert second.written == 0
    assert second.already_held == 2
    assert len(list((project / "output" / "markdown").glob("*.md"))) == 1
    # Only the archive listing was fetched — no article page was requested.
    assert all("/p/" not in url for url in fetcher.requested)


def test_failed_article_does_not_stop_the_run(project, mapping_store, stub_fetcher) -> None:
    stub_fetcher(
        {"https://x.test/api?offset=0&limit=10": archive_json("Good One", "Bad One"),
         "https://x.test/api?offset=10&limit=10": "[]"},
        fail={"https://x.test/p/bad-one"},
    )
    result = make_runner(mapping_store).run("Example")

    assert result.written == 1
    assert result.failed == 1

    with BackfillTracker.beside(Path("data/gmail_ingestor.db")) as tracker:
        assert tracker.count_by_status("Example") == {"done": 1, "failed": 1}


def test_failed_article_is_retried_next_run(project, mapping_store, stub_fetcher) -> None:
    """A 404 today may be a live page tomorrow, so failures stay eligible."""
    stub_fetcher(
        {"https://x.test/api?offset=0&limit=10": archive_json("Flaky"),
         "https://x.test/api?offset=10&limit=10": "[]"},
        fail={"https://x.test/p/flaky"},
    )
    assert make_runner(mapping_store).run("Example").failed == 1

    stub_fetcher({"https://x.test/api?offset=0&limit=10": archive_json("Flaky"),
                  "https://x.test/api?offset=10&limit=10": "[]"})
    assert make_runner(mapping_store).run("Example").written == 1


def test_stop_halts_between_articles(project, mapping_store, stub_fetcher) -> None:
    """Completed work is committed; nothing is left half-written."""
    stub_fetcher({
        "https://x.test/api?offset=0&limit=10": archive_json("One", "Two", "Three"),
        "https://x.test/api?offset=10&limit=10": "[]",
    })

    def should_stop() -> bool:
        # Stop once the first article has been written.
        return len(list((project / "output" / "markdown").glob("*.md"))) >= 1

    result = make_runner(mapping_store, should_stop=should_stop).run("Example")
    assert result.written == 1

    md = list((project / "output" / "markdown").glob("*.md"))
    raw = list((project / "output" / "raw").glob("*.html"))
    assert len(md) == len(raw) == 1


def test_run_records_an_audit_row(project, mapping_store, stub_fetcher) -> None:
    stub_fetcher(ONE_PAGE)
    make_runner(mapping_store).run("Example")

    with BackfillTracker.beside(Path("data/gmail_ingestor.db")) as tracker:
        run = tracker.last_run("Example")
    assert run["articles_listed"] == 2
    assert run["articles_written"] == 1
    assert run["completed_at"] is not None


def test_gmail_database_is_not_modified(project, mapping_store, stub_fetcher) -> None:
    """The whole point of a separate DB: gmail_ingestor.db is read-only here."""
    gmail_db = project / "data" / "gmail_ingestor.db"
    before = gmail_db.read_bytes()

    stub_fetcher(ONE_PAGE)
    make_runner(mapping_store).run("Example")

    assert gmail_db.read_bytes() == before
    assert (project / "data" / "backfill.db").exists()


def test_missing_label_id_falls_back_to_markdown(project, mapping_store, stub_fetcher) -> None:
    """A stale label_id must not silently re-backfill an archive we hold."""
    (project / "output" / "markdown" / "already-held_aaa.md").write_text(
        '---\nid: "aaa"\nsubject: "Already Held"\nlabels: ["Example"]\n---\nbody\n',
        encoding="utf-8",
    )
    entry = json.loads((mapping_store.path).read_text())
    entry["mappings"]["Example"]["label_id"] = "Label_GONE"
    mapping_store.path.write_text(json.dumps(entry))

    stub_fetcher(ONE_PAGE)
    entries = make_runner(mapping_store).scan("Example")

    by_title = {e.ref.title: e for e in entries}
    assert by_title["Already Held"].status == "have"
    assert by_title["A New Post"].is_missing
