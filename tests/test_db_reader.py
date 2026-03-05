"""Tests for DBReader — read-only SQLite access."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ingestor_tui.db_reader import DBReader


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a test SQLite database with the gmail-ingestor schema."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE messages (
            message_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            label_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            subject TEXT DEFAULT '',
            sender TEXT DEFAULT '',
            date TEXT DEFAULT '',
            raw_text_path TEXT DEFAULT '',
            raw_html_path TEXT DEFAULT '',
            markdown_path TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE fetch_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            label_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            ids_discovered INTEGER DEFAULT 0,
            messages_fetched INTEGER DEFAULT 0,
            messages_converted INTEGER DEFAULT 0,
            messages_failed INTEGER DEFAULT 0
        );
    """)
    conn.close()
    return path


@pytest.fixture
def populated_db(db_path: Path) -> Path:
    """Insert test data into the database."""
    conn = sqlite3.connect(str(db_path))
    now = datetime.now(UTC).isoformat()

    messages = [
        ("msg1", "t1", "INBOX", "pending", "Subject 1", "a@b.com", now, now),
        ("msg2", "t2", "INBOX", "pending", "Subject 2", "c@d.com", now, now),
        ("msg3", "t3", "INBOX", "fetched", "Subject 3", "e@f.com", now, now),
        ("msg4", "t4", "INBOX", "converted", "Subject 4", "g@h.com", now, now),
        ("msg5", "t5", "INBOX", "converted", "Subject 5", "i@j.com", now, now),
        ("msg6", "t6", "INBOX", "converted", "Subject 6", "k@l.com", now, now),
        ("msg7", "t7", "INBOX", "failed", "Subject 7", "m@n.com", now, now),
    ]
    conn.executemany(
        "INSERT INTO messages (message_id, thread_id, label_id, status, subject, sender, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        messages,
    )

    conn.execute(
        "INSERT INTO fetch_runs (label_id, started_at, completed_at, ids_discovered, messages_fetched, messages_converted, messages_failed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("INBOX", now, now, 7, 5, 3, 1),
    )
    conn.commit()
    conn.close()
    return db_path


class TestDBReader:
    def test_exists_false(self, tmp_path: Path) -> None:
        reader = DBReader(tmp_path / "nonexistent.db")
        assert not reader.exists

    def test_exists_true(self, db_path: Path) -> None:
        reader = DBReader(db_path)
        assert reader.exists

    def test_count_by_status_empty(self, db_path: Path) -> None:
        reader = DBReader(db_path)
        assert reader.count_by_status() == {}

    def test_count_by_status_populated(self, populated_db: Path) -> None:
        reader = DBReader(populated_db)
        counts = reader.count_by_status()
        assert counts == {"pending": 2, "fetched": 1, "converted": 3, "failed": 1}

    def test_total_messages_empty(self, db_path: Path) -> None:
        reader = DBReader(db_path)
        assert reader.total_messages() == 0

    def test_total_messages_populated(self, populated_db: Path) -> None:
        reader = DBReader(populated_db)
        assert reader.total_messages() == 7

    def test_last_run_empty(self, db_path: Path) -> None:
        reader = DBReader(db_path)
        assert reader.last_run() is None

    def test_last_run_populated(self, populated_db: Path) -> None:
        reader = DBReader(populated_db)
        run = reader.last_run()
        assert run is not None
        assert run["label_id"] == "INBOX"
        assert run["ids_discovered"] == 7
        assert run["messages_fetched"] == 5
        assert run["messages_converted"] == 3
        assert run["messages_failed"] == 1

    def test_get_recent_messages(self, populated_db: Path) -> None:
        reader = DBReader(populated_db)
        messages = reader.get_recent_messages(limit=3)
        assert len(messages) == 3
        assert all("message_id" in m for m in messages)
        assert all("status" in m for m in messages)

    def test_get_sync_state_missing_table(self, db_path: Path) -> None:
        """get_sync_state should return empty list when sync_state table doesn't exist."""
        reader = DBReader(db_path)
        assert reader.get_sync_state() == []

    def test_get_sync_state_with_data(self, db_path: Path) -> None:
        """get_sync_state should return rows from the sync_state table."""
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE labels (
                label_id TEXT PRIMARY KEY,
                label_name TEXT NOT NULL
            );
            CREATE TABLE sync_state (
                label_id TEXT PRIMARY KEY,
                history_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO labels (label_id, label_name) VALUES ('INBOX', 'Inbox');
            INSERT INTO sync_state (label_id, history_id, updated_at)
                VALUES ('INBOX', '12345', '2026-03-05T10:00:00');
        """)
        conn.close()

        reader = DBReader(db_path)
        result = reader.get_sync_state()
        assert len(result) == 1
        assert result[0]["label_id"] == "INBOX"
        assert result[0]["label_name"] == "Inbox"
        assert result[0]["history_id"] == "12345"

    def test_get_sync_state_no_label_match(self, db_path: Path) -> None:
        """get_sync_state should handle labels not in the labels table (LEFT JOIN)."""
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE labels (
                label_id TEXT PRIMARY KEY,
                label_name TEXT NOT NULL
            );
            CREATE TABLE sync_state (
                label_id TEXT PRIMARY KEY,
                history_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO sync_state (label_id, history_id, updated_at)
                VALUES ('UNKNOWN', '999', '2026-03-05T10:00:00');
        """)
        conn.close()

        reader = DBReader(db_path)
        result = reader.get_sync_state()
        assert len(result) == 1
        assert result[0]["label_name"] is None
        assert result[0]["label_id"] == "UNKNOWN"

    def test_nonexistent_db_returns_defaults(self, tmp_path: Path) -> None:
        reader = DBReader(tmp_path / "nope.db")
        assert reader.count_by_status() == {}
        assert reader.total_messages() == 0
        assert reader.last_run() is None
        assert reader.get_recent_messages() == []
        assert reader.get_sync_state() == []
