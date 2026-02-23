"""Read-only SQLite access for dashboard state.

Uses short-lived connections to avoid holding locks. The gmail-ingestor DB
uses WAL mode, so reads don't block writes (and vice versa).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class DBReader:
    """Read-only queries against the gmail-ingestor SQLite database."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @property
    def exists(self) -> bool:
        return self._db_path.exists()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def count_by_status(self) -> dict[str, int]:
        """Get message counts grouped by status."""
        if not self.exists:
            return {}
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM messages GROUP BY status"
            ).fetchall()
            return {row["status"]: row["cnt"] for row in rows}
        finally:
            conn.close()

    def total_messages(self) -> int:
        """Get total message count."""
        if not self.exists:
            return 0
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM messages").fetchone()
            return row["cnt"] if row else 0
        finally:
            conn.close()

    def last_run(self) -> dict | None:
        """Get the most recent fetch run record."""
        if not self.exists:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM fetch_runs ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_recent_messages(self, limit: int = 20) -> list[dict]:
        """Get recently updated messages."""
        if not self.exists:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT message_id, subject, sender, status, date, updated_at "
                "FROM messages ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
