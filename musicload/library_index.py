"""Small persistent index used to avoid downloading the same song twice."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import UTC, datetime
from pathlib import Path


def _database_path(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "library-index.sqlite3"


def _connect(data_dir: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(_database_path(data_dir), timeout=5)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS downloaded_tracks (
            video_id TEXT PRIMARY KEY,
            identity_key TEXT NOT NULL,
            file_path TEXT NOT NULL,
            downloaded_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_downloaded_tracks_identity "
        "ON downloaded_tracks(identity_key)"
    )
    return connection


def _normalise(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"\W+", " ", text, flags=re.UNICODE).strip()


def build_identity(info: dict) -> str:
    """Create a stable fallback key for the same recording from another video."""
    artist = info.get("artist") or info.get("uploader") or ""
    title = info.get("title") or ""
    album = info.get("album") or ""
    duration = round(float(info.get("duration") or 0))
    return "|".join((_normalise(artist), _normalise(title), _normalise(album), str(duration)))


def find_existing_download(data_dir: Path, video_id: str, info: dict) -> Path | None:
    """Return a still-existing indexed file for this video or recording."""
    identity_key = build_identity(info)
    connection = _connect(data_dir)
    try:
        rows = connection.execute(
            "SELECT video_id, file_path FROM downloaded_tracks "
            "WHERE video_id = ? OR identity_key = ? "
            "ORDER BY CASE WHEN video_id = ? THEN 0 ELSE 1 END",
            (video_id, identity_key, video_id),
        ).fetchall()
        for indexed_video_id, indexed_path in rows:
            path = Path(indexed_path)
            if path.is_file():
                return path
            connection.execute("DELETE FROM downloaded_tracks WHERE video_id = ?", (indexed_video_id,))
        connection.commit()
    finally:
        connection.close()
    return None


def record_download(data_dir: Path, video_id: str, info: dict, audio_path: Path) -> None:
    """Remember a completed download, including existing files found on disk."""
    connection = _connect(data_dir)
    try:
        connection.execute(
            """
            INSERT INTO downloaded_tracks (video_id, identity_key, file_path, downloaded_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                identity_key = excluded.identity_key,
                file_path = excluded.file_path,
                downloaded_at = excluded.downloaded_at
            """,
            (
                video_id,
                build_identity(info),
                str(audio_path),
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
