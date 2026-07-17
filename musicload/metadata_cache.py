"""SQLite-backed metadata cache for YouTube Music track lookups.

Caches song metadata and track info by video_id to avoid redundant
YouTube Music API calls on subsequent syncs/downloads. Results are
stable per video_id and safe to cache permanently (no TTL).

Inspired by yubal's ExtractionCache:
https://github.com/guillevc/yubal/blob/master/packages/yubal/src/yubal/services/cache.py
"""

import logging
import sqlite3
import time
from pathlib import Path
from types import TracebackType

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class CachedSongMetadata(BaseModel):
    """Cached song metadata for lyrics lookup (maps to search.SongMetadata)."""

    video_id: str
    title: str
    artist: str
    album: str | None
    duration_seconds: int


class CachedTrack(BaseModel):
    """Cached track info for URL lookups (maps to search.Track)."""

    video_id: str
    title: str
    artist: str
    artists: list[str]
    album: str | None
    duration_seconds: int
    thumbnail_url: str | None
    view_count: str | None
    video_type: str | None = None


class CachedLyrics(BaseModel):
    """Cached lyrics lookup result.

    Stores both positive results (lyrics text) and negative results (no lyrics found).
    Negative results use a TTL to allow re-lookup after lyrics may have been added.
    """

    video_id: str
    lyrics: str | None  # None = negative cache (no lyrics found)
    cached_at: float  # time.time() for TTL check on negatives


_SCHEMA_STATEMENTS = [
    "CREATE TABLE IF NOT EXISTS song_metadata (video_id TEXT PRIMARY KEY, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS track (video_id TEXT PRIMARY KEY, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS lyrics (video_id TEXT PRIMARY KEY, data TEXT NOT NULL)",
]


class MetadataCache:
    """SQLite cache for YouTube Music metadata lookups.

    Usage::

        with MetadataCache(cache_dir) as cache:
            cached = cache.get_song_metadata(video_id)
            if cached is not None:
                ...
    """

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._db_path = cache_dir / "metadata_cache.db"
        self._conn: sqlite3.Connection | None = None

    def load(self) -> None:
        """Open the database and ensure schema exists."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), timeout=5)
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                logger.debug(
                    "WAL mode unavailable for %s (network filesystem?), using default journal mode",
                    self._db_path,
                )
            for stmt in _SCHEMA_STATEMENTS:
                self._conn.execute(stmt)
            self._conn.commit()
        except Exception:
            logger.warning("Failed to open metadata cache at %s", self._db_path, exc_info=True)
            self._conn = None

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> "MetadataCache":
        self.load()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- song_metadata table --

    def get_song_metadata(self, video_id: str) -> CachedSongMetadata | None:
        """Look up cached song metadata by video_id."""
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT data FROM song_metadata WHERE video_id = ?", (video_id,)
            ).fetchone()
            if row is None:
                return None
            return CachedSongMetadata.model_validate_json(row[0])
        except Exception:
            logger.debug("Cache read error for song_metadata/%s", video_id, exc_info=True)
            return None

    def add_song_metadata(self, metadata: CachedSongMetadata) -> None:
        """Store song metadata (INSERT OR REPLACE)."""
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO song_metadata (video_id, data) VALUES (?, ?)",
                (metadata.video_id, metadata.model_dump_json()),
            )
            self._conn.commit()
        except Exception:
            logger.debug("Cache write error for song_metadata/%s", metadata.video_id, exc_info=True)

    # -- track table --

    def get_track(self, video_id: str) -> CachedTrack | None:
        """Look up cached track info by video_id."""
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT data FROM track WHERE video_id = ?", (video_id,)
            ).fetchone()
            if row is None:
                return None
            return CachedTrack.model_validate_json(row[0])
        except Exception:
            logger.debug("Cache read error for track/%s", video_id, exc_info=True)
            return None

    def add_track(self, track: CachedTrack) -> None:
        """Store track info (INSERT OR REPLACE)."""
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO track (video_id, data) VALUES (?, ?)",
                (track.video_id, track.model_dump_json()),
            )
            self._conn.commit()
        except Exception:
            logger.debug("Cache write error for track/%s", track.video_id, exc_info=True)

    # -- lyrics table --

    def get_lyrics(self, video_id: str, negative_ttl_hours: int = 168) -> CachedLyrics | None:
        """Look up cached lyrics by video_id.

        Returns CachedLyrics if found and valid, None if not cached or expired.
        Positive results never expire. Negative results (lyrics=None) expire
        after negative_ttl_hours.
        """
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT data FROM lyrics WHERE video_id = ?", (video_id,)
            ).fetchone()
            if row is None:
                return None
            cached = CachedLyrics.model_validate_json(row[0])
            # Positive results never expire
            if cached.lyrics is not None:
                return cached
            # Negative results expire after TTL
            if negative_ttl_hours <= 0:
                # TTL disabled — negatives never expire
                return cached
            age_hours = (time.time() - cached.cached_at) / 3600
            if age_hours < negative_ttl_hours:
                return cached
            # Expired negative — delete and return None
            self._conn.execute("DELETE FROM lyrics WHERE video_id = ?", (video_id,))
            self._conn.commit()
            return None
        except Exception:
            logger.debug("Cache read error for lyrics/%s", video_id, exc_info=True)
            return None

    def add_lyrics(self, entry: CachedLyrics) -> None:
        """Store lyrics result (INSERT OR REPLACE)."""
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO lyrics (video_id, data) VALUES (?, ?)",
                (entry.video_id, entry.model_dump_json()),
            )
            self._conn.commit()
        except Exception:
            logger.debug("Cache write error for lyrics/%s", entry.video_id, exc_info=True)
