"""Per-account ListenBrainz settings for the optional manual web explorer."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(data_dir / "web-users.sqlite3", timeout=5)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS listenbrainz_users (
            account_name TEXT PRIMARY KEY,
            listenbrainz_username TEXT NOT NULL
        )
        """
    )
    return connection


def get_listenbrainz_username(data_dir: Path, account_name: str) -> str | None:
    """Return the ListenBrainz username saved for one Musicload account."""
    connection = _connect(data_dir)
    try:
        row = connection.execute(
            "SELECT listenbrainz_username FROM listenbrainz_users WHERE account_name = ?",
            (account_name,),
        ).fetchone()
        return str(row[0]) if row else None
    finally:
        connection.close()


def set_listenbrainz_username(
    data_dir: Path, account_name: str, listenbrainz_username: str
) -> None:
    """Create or replace the ListenBrainz username for one Musicload account."""
    connection = _connect(data_dir)
    try:
        connection.execute(
            """
            INSERT INTO listenbrainz_users (account_name, listenbrainz_username)
            VALUES (?, ?)
            ON CONFLICT(account_name) DO UPDATE SET
                listenbrainz_username = excluded.listenbrainz_username
            """,
            (account_name, listenbrainz_username),
        )
        connection.commit()
    finally:
        connection.close()

