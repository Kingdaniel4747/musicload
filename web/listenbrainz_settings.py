"""Per-account ListenBrainz settings for the optional manual web explorer."""

from __future__ import annotations

import sqlite3
import json
from datetime import UTC, datetime
from pathlib import Path


def _connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(data_dir / "web-users.sqlite3", timeout=5)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS listenbrainz_users (
            account_name TEXT PRIMARY KEY,
            listenbrainz_username TEXT NOT NULL,
            auto_download INTEGER NOT NULL DEFAULT 0,
            download_weekday INTEGER NOT NULL DEFAULT 0,
            download_time TEXT NOT NULL DEFAULT '03:00',
            timezone TEXT NOT NULL DEFAULT 'UTC',
            last_run_date TEXT,
            last_download_hash TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS listenbrainz_cache (
            listenbrainz_username TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(listenbrainz_users)")
    }
    migrations = {
        "auto_download": "INTEGER NOT NULL DEFAULT 0",
        "download_weekday": "INTEGER NOT NULL DEFAULT 0",
        "download_time": "TEXT NOT NULL DEFAULT '03:00'",
        "timezone": "TEXT NOT NULL DEFAULT 'UTC'",
        "last_run_date": "TEXT",
        "last_download_hash": "TEXT",
    }
    for name, definition in migrations.items():
        if name not in columns:
            connection.execute(
                f"ALTER TABLE listenbrainz_users ADD COLUMN {name} {definition}"
            )
    connection.commit()
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


def get_listenbrainz_settings(data_dir: Path, account_name: str) -> dict | None:
    """Return all manual explorer and scheduler settings for one account."""
    connection = _connect(data_dir)
    try:
        row = connection.execute(
            """SELECT listenbrainz_username, auto_download, download_weekday, download_time,
                      timezone, last_run_date, last_download_hash
               FROM listenbrainz_users WHERE account_name = ?""",
            (account_name,),
        ).fetchone()
        if not row:
            return None
        return {
            "username": row[0],
            "auto_download": bool(row[1]),
            "download_weekday": row[2],
            "download_time": row[3],
            "timezone": row[4],
            "last_run_date": row[5],
            "last_download_hash": row[6],
        }
    finally:
        connection.close()


def set_listenbrainz_settings(data_dir: Path, account_name: str, settings: dict) -> None:
    """Save username and optional weekly automatic-download schedule."""
    connection = _connect(data_dir)
    try:
        connection.execute(
            """
            INSERT INTO listenbrainz_users
                (account_name, listenbrainz_username, auto_download, download_weekday, download_time, timezone)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_name) DO UPDATE SET
                last_run_date = CASE
                    WHEN listenbrainz_users.listenbrainz_username != excluded.listenbrainz_username
                    THEN NULL ELSE listenbrainz_users.last_run_date END,
                last_download_hash = CASE
                    WHEN listenbrainz_users.listenbrainz_username != excluded.listenbrainz_username
                    THEN NULL ELSE listenbrainz_users.last_download_hash END,
                listenbrainz_username = excluded.listenbrainz_username,
                auto_download = excluded.auto_download,
                download_weekday = excluded.download_weekday,
                download_time = excluded.download_time,
                timezone = excluded.timezone
            """,
            (
                account_name,
                settings["username"],
                int(bool(settings.get("auto_download"))),
                int(settings.get("download_weekday", 0)),
                settings.get("download_time") or "03:00",
                settings.get("timezone") or "UTC",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def list_listenbrainz_settings(data_dir: Path) -> list[dict]:
    """List configured accounts for cache warming and scheduling."""
    connection = _connect(data_dir)
    try:
        rows = connection.execute(
            """SELECT account_name, listenbrainz_username, auto_download,
                      download_weekday, download_time, timezone, last_run_date, last_download_hash
               FROM listenbrainz_users"""
        ).fetchall()
        return [
            {
                "account_name": row[0],
                "username": row[1],
                "auto_download": bool(row[2]),
                "download_weekday": row[3],
                "download_time": row[4],
                "timezone": row[5],
                "last_run_date": row[6],
                "last_download_hash": row[7],
            }
            for row in rows
        ]
    finally:
        connection.close()


def mark_listenbrainz_run(
    data_dir: Path, account_name: str, local_date: str, playlist_hash: str
) -> None:
    connection = _connect(data_dir)
    try:
        connection.execute(
            """UPDATE listenbrainz_users
               SET last_run_date = ?, last_download_hash = ? WHERE account_name = ?""",
            (local_date, playlist_hash, account_name),
        )
        connection.commit()
    finally:
        connection.close()


def get_cached_recommendations(data_dir: Path, username: str) -> dict | None:
    """Return persistent matched recommendations, including cache timestamp."""
    connection = _connect(data_dir)
    try:
        row = connection.execute(
            "SELECT payload, updated_at FROM listenbrainz_cache WHERE listenbrainz_username = ?",
            (username.casefold(),),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        payload["cached_at"] = row[1]
        return payload
    finally:
        connection.close()


def set_cached_recommendations(data_dir: Path, username: str, payload: dict) -> None:
    """Persist matched recommendations across container restarts."""
    stored = dict(payload)
    stored.pop("cached_at", None)
    connection = _connect(data_dir)
    try:
        connection.execute(
            """INSERT OR REPLACE INTO listenbrainz_cache
               (listenbrainz_username, payload, updated_at) VALUES (?, ?, ?)""",
            (
                username.casefold(),
                json.dumps(stored, ensure_ascii=False),
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
