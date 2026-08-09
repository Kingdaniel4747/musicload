"""Persistent metadata cache for the Local Files view."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(data_dir / "library-cache.sqlite3", timeout=10)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS library_files (
            entry_path TEXT PRIMARY KEY,
            modified_at REAL NOT NULL,
            file_size INTEGER NOT NULL,
            metadata TEXT NOT NULL
        )
        """
    )
    return connection


def set_cached_file(
    data_dir: Path,
    entry_path: str,
    modified_at: float,
    file_size: int,
    metadata: dict,
) -> None:
    connection = _connect(data_dir)
    try:
        connection.execute(
            """INSERT OR REPLACE INTO library_files
               (entry_path, modified_at, file_size, metadata) VALUES (?, ?, ?, ?)""",
            (entry_path, modified_at, file_size, json.dumps(metadata, ensure_ascii=False)),
        )
        connection.commit()
    finally:
        connection.close()


def load_cached_files(data_dir: Path) -> dict[str, tuple[float, int, dict]]:
    """Load the full lightweight index in one SQLite query."""
    connection = _connect(data_dir)
    try:
        return {
            row[0]: (row[1], row[2], json.loads(row[3]))
            for row in connection.execute(
                "SELECT entry_path, modified_at, file_size, metadata FROM library_files"
            )
        }
    finally:
        connection.close()


def replace_cached_files(
    data_dir: Path, entries: dict[str, tuple[float, int, dict]]
) -> None:
    """Persist a complete startup scan with a single transaction."""
    connection = _connect(data_dir)
    try:
        connection.execute("DELETE FROM library_files")
        connection.executemany(
            """INSERT INTO library_files
               (entry_path, modified_at, file_size, metadata) VALUES (?, ?, ?, ?)""",
            [
                (path, modified_at, size, json.dumps(metadata, ensure_ascii=False))
                for path, (modified_at, size, metadata) in entries.items()
            ],
        )
        connection.commit()
    finally:
        connection.close()
