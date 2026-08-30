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

    def get_sync_state(self) -> list[dict]:
        """Get incremental sync state for all labels."""
        if not self.exists:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT s.label_id, l.label_name, s.history_id, s.updated_at "
                "FROM sync_state s "
                "LEFT JOIN labels l ON s.label_id = l.label_id "
                "ORDER BY s.updated_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            # Table may not exist yet if no sync has been performed
            return []
        finally:
            conn.close()

    def get_messages_for_label(self, label_id: str) -> list[dict]:
        """Get every message carrying a label, for backfill gap detection.

        Joins through `message_labels` rather than filtering `messages.label_id`:
        the latter records only the label a message was *discovered* under, so a
        newsletter found via INBOX would be invisible to a per-label query.
        """
        if not self.exists:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT m.message_id, m.subject, m.date "
                "FROM messages m "
                "JOIN message_labels ml ON ml.message_id = m.message_id "
                "WHERE ml.label_id = ? "
                "ORDER BY m.date DESC",
                (label_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            # message_labels may not exist on an older database
            return []
        finally:
            conn.close()

    def get_label_id(self, label_name: str) -> str | None:
        """Resolve a Gmail label name to its ID, or None if unknown."""
        if not self.exists:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT label_id FROM labels WHERE label_name = ?", (label_name,)
            ).fetchone()
            return row["label_id"] if row else None
        except sqlite3.OperationalError:
            return None
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
