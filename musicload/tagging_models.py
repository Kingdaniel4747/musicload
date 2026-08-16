"""Data models used while tagging existing audio files."""

from dataclasses import dataclass


@dataclass
class TagStats:
    """Accumulated statistics from a tagging run."""

    files_found: int = 0
    lyrics_added: int = 0
    lyrics_skipped: int = 0
    lyrics_not_found: int = 0
    lyrics_failed: int = 0
    replaygain_applied: int = 0
    replaygain_skipped: int = 0
    replaygain_failed: int = 0
    metadata_enriched: int = 0
    metadata_skipped: int = 0
    metadata_failed: int = 0
    errors: int = 0


@dataclass
class FileMetadata:
    """Metadata extracted from an audio file."""

    title: str
    artist: str
    album: str | None
    duration_seconds: int


@dataclass
class PartialMetadata:
    """Partial metadata from an audio file — values may be None if missing."""

    title: str | None
    artist: str | None
    album: str | None
    duration_seconds: int
    has_cover: bool
