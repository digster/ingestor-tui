"""SQLite state for backfilled articles — a sibling of the gmail-ingestor DB.

Deliberately a **separate database file**. Backfilled articles are not Gmail
messages, and putting them in ``messages`` would hand them to machinery that
has no idea what they are: ``run_convert_pending`` would try to re-convert rows
whose ``raw_text_path`` points at nothing, and ``retry_failed`` would reset
them. Keeping them apart means gmail_ingestor.db is opened read-only by this
feature and its schema is untouched.

The API intentionally mirrors ``gmail_ingestor.storage.tracker.FetchTracker``
so the two read alike.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ingestor_tui.backfill.models import VALID_STATUSES

logger = logging.getLogger(__name__)

DB_FILENAME = "backfill.db"


class BackfillTracker:
    """Tracks backfilled article state in SQLite.

    Tables:
    - backfill_articles: per-article state, keyed by derived article ID
    - backfill_runs: audit log of backfill runs
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @classmethod
    def beside(cls, gmail_db_path: Path) -> BackfillTracker:
        """Place backfill.db in the same directory as the gmail-ingestor DB."""
        return cls(Path(gmail_db_path).parent / DB_FILENAME)

    @property
    def path(self) -> Path:
        return self._db_path

    def connect(self) -> None:
        """Open the connection and ensure the schema exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> BackfillTracker:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    def _create_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS backfill_articles (
                article_id TEXT PRIMARY KEY,
                label_name TEXT NOT NULL,
                label_id TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL UNIQUE,
                title TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'discovered',
                match_reason TEXT DEFAULT '',
                raw_html_path TEXT DEFAULT '',
                markdown_path TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_backfill_label
                ON backfill_articles(label_name);
            CREATE INDEX IF NOT EXISTS idx_backfill_status
                ON backfill_articles(status);

            CREATE TABLE IF NOT EXISTS backfill_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                label_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                articles_listed INTEGER DEFAULT 0,
                articles_held INTEGER DEFAULT 0,
                articles_written INTEGER DEFAULT 0,
                articles_failed INTEGER DEFAULT 0,
                dry_run INTEGER DEFAULT 0
            );
        """)
        self.conn.commit()

    # --- articles ---

    def record_article(
        self,
        *,
        article_id: str,
        label_name: str,
        label_id: str,
        url: str,
        title: str = "",
        published_at: str = "",
        status: str = "discovered",
        match_reason: str = "",
    ) -> None:
        """Insert or update an article row.

        ``url`` is UNIQUE as well as the ID being a hash of it, so a publisher
        who exposes the same post twice in a listing cannot produce two rows.
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")

        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            """INSERT INTO backfill_articles
                   (article_id, label_name, label_id, url, title, published_at,
                    status, match_reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(article_id) DO UPDATE SET
                   label_name = excluded.label_name,
                   label_id = excluded.label_id,
                   title = excluded.title,
                   published_at = excluded.published_at,
                   -- `done` is a statement about the filesystem: this article
                   -- has a markdown file and a raw body on disk. A re-scan
                   -- reclassifies it as `have` (classify() reports an
                   -- already-backfilled URL that way), and letting that
                   -- overwrite `done` would lose the paths and make the row
                   -- stop describing what backfill actually wrote — which in
                   -- turn hides the files from `prune`. mark_done/mark_failed
                   -- still set the status outright; only this upsert defers.
                   status = CASE
                       WHEN backfill_articles.status = 'done' THEN 'done'
                       ELSE excluded.status
                   END,
                   match_reason = excluded.match_reason,
                   updated_at = excluded.updated_at""",
            (article_id, label_name, label_id, url, title, published_at,
             status, match_reason, now, now),
        )
        self.conn.commit()

    def mark_done(
        self,
        article_id: str,
        *,
        raw_html_path: str,
        markdown_path: str,
    ) -> None:
        """Record a successfully written article."""
        self.conn.execute(
            """UPDATE backfill_articles
               SET status = 'done', raw_html_path = ?, markdown_path = ?,
                   error_message = '', updated_at = ?
               WHERE article_id = ?""",
            (raw_html_path, markdown_path, datetime.now(UTC).isoformat(), article_id),
        )
        self.conn.commit()

    def mark_failed(self, article_id: str, error_message: str) -> None:
        """Record a failed article, keeping the error for the operator."""
        self.conn.execute(
            """UPDATE backfill_articles
               SET status = 'failed', error_message = ?, updated_at = ?
               WHERE article_id = ?""",
            (error_message[:1000], datetime.now(UTC).isoformat(), article_id),
        )
        self.conn.commit()

    def completed_urls(self, label_name: str) -> set[str]:
        """Canonical URLs already written for a label.

        Only ``done`` rows count. A ``failed`` row must stay eligible for a
        retry on the next run, and a ``discovered`` row was never fetched.
        """
        rows = self.conn.execute(
            "SELECT url FROM backfill_articles WHERE label_name = ? AND status = 'done'",
            (label_name,),
        ).fetchall()
        return {row["url"] for row in rows}

    def articles_for_label(self, label_name: str) -> list[dict]:
        """Every recorded article for a label, whatever its status.

        Ordered by publish date so a prune preview reads chronologically rather
        than in insertion order.
        """
        rows = self.conn.execute(
            "SELECT * FROM backfill_articles WHERE label_name = ? "
            "ORDER BY published_at DESC, article_id",
            (label_name,),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_articles(self, article_ids: list[str]) -> int:
        """Forget the given articles so a later scan rediscovers their URLs.

        Deleting the row is the point: ``completed_urls`` reads ``done`` rows to
        decide what to skip, so leaving one behind after removing its files
        would make the article permanently invisible to both the corpus scan and
        the backfill scan.
        """
        if not article_ids:
            return 0

        # Chunked to stay under SQLite's variable limit (999 by default), which
        # a whole-label prune can otherwise exceed.
        deleted = 0
        for start in range(0, len(article_ids), 500):
            chunk = article_ids[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            cursor = self.conn.execute(
                f"DELETE FROM backfill_articles WHERE article_id IN ({placeholders})",
                chunk,
            )
            deleted += cursor.rowcount
        self.conn.commit()
        return deleted

    def get_article(self, article_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM backfill_articles WHERE article_id = ?", (article_id,)
        ).fetchone()
        return dict(row) if row else None

    def count_by_status(self, label_name: str | None = None) -> dict[str, int]:
        """Article counts grouped by status, optionally scoped to one label."""
        if label_name:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM backfill_articles "
                "WHERE label_name = ? GROUP BY status",
                (label_name,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM backfill_articles GROUP BY status"
            ).fetchall()
        return {row["status"]: row["cnt"] for row in rows}

    # --- runs ---

    def start_run(self, label_name: str, *, dry_run: bool = False) -> int:
        """Record the start of a backfill run. Returns the run_id."""
        cursor = self.conn.execute(
            "INSERT INTO backfill_runs (label_name, started_at, dry_run) VALUES (?, ?, ?)",
            (label_name, datetime.now(UTC).isoformat(), int(dry_run)),
        )
        self.conn.commit()
        return cursor.lastrowid or 0

    def complete_run(
        self,
        run_id: int,
        *,
        articles_listed: int = 0,
        articles_held: int = 0,
        articles_written: int = 0,
        articles_failed: int = 0,
    ) -> None:
        self.conn.execute(
            """UPDATE backfill_runs SET
                   completed_at = ?, articles_listed = ?, articles_held = ?,
                   articles_written = ?, articles_failed = ?
               WHERE run_id = ?""",
            (datetime.now(UTC).isoformat(), articles_listed, articles_held,
             articles_written, articles_failed, run_id),
        )
        self.conn.commit()

    def last_run(self, label_name: str) -> dict | None:
        """Most recent run record for a label, or None."""
        row = self.conn.execute(
            "SELECT * FROM backfill_runs WHERE label_name = ? ORDER BY run_id DESC LIMIT 1",
            (label_name,),
        ).fetchone()
        return dict(row) if row else None
