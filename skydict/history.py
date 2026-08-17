"""Dictation history in SQLite.

Kept so a dictation is never lost to a mis-aimed paste: the menubar can copy any recent
entry back to the clipboard. Stored beside the config in Application Support.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import config_dir
from .pipeline import DictationResult

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    backend     TEXT    NOT NULL,
    model       TEXT    NOT NULL,
    language    TEXT,
    duration    REAL
);
CREATE INDEX IF NOT EXISTS dictations_created_at ON dictations (created_at DESC);
"""


@dataclass(slots=True)
class Entry:
    id: int
    created_at: datetime
    text: str
    backend: str
    model: str
    language: str | None = None
    duration: float | None = None

    @property
    def preview(self) -> str:
        """A single line short enough for a menu item."""
        collapsed = " ".join(self.text.split())
        return collapsed if len(collapsed) <= 60 else collapsed[:57] + "…"


def history_path() -> Path:
    return config_dir() / "history.db"


class History:
    """Append-only log of dictations, newest first.

    Each call opens its own connection: SQLite connections are not shareable across
    threads, and dictations arrive on a worker thread while the menubar reads on the
    main one.
    """

    def __init__(self, path: Path | None = None, limit: int | None = 500) -> None:
        self.path = path or history_path()
        #: Rows kept before the oldest are pruned. None disables pruning.
        self.limit = limit
        self._lock = threading.Lock()
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def add(self, result: DictationResult) -> int | None:
        """Record a dictation. Empty text is not stored. Returns the new row id."""
        if not result.text.strip():
            return None

        transcript = result.transcript
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO dictations (created_at, text, backend, model, language, duration) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    result.text,
                    transcript.backend,
                    transcript.model,
                    transcript.language,
                    result.audio_duration,
                ),
            )
            row_id = cursor.lastrowid
            self._prune(connection)
        return row_id

    def _prune(self, connection: sqlite3.Connection) -> None:
        if self.limit is None:
            return
        connection.execute(
            "DELETE FROM dictations WHERE id NOT IN "
            "(SELECT id FROM dictations ORDER BY id DESC LIMIT ?)",
            (self.limit,),
        )

    def recent(self, count: int = 10) -> list[Entry]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM dictations ORDER BY id DESC LIMIT ?", (count,)
            ).fetchall()
        return [self._to_entry(row) for row in rows]

    def search(self, query: str, count: int = 50) -> list[Entry]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM dictations WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{query}%", count),
            ).fetchall()
        return [self._to_entry(row) for row in rows]

    def get(self, entry_id: int) -> Entry | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dictations WHERE id = ?", (entry_id,)
            ).fetchone()
        return self._to_entry(row) if row else None

    def delete(self, entry_id: int) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM dictations WHERE id = ?", (entry_id,))
        return cursor.rowcount > 0

    def clear(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM dictations")

    def count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM dictations").fetchone()[0]

    @staticmethod
    def _to_entry(row: sqlite3.Row) -> Entry:
        return Entry(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            text=row["text"],
            backend=row["backend"],
            model=row["model"],
            language=row["language"],
            duration=row["duration"],
        )
