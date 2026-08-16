"""Tag existing audio files with lyrics, ReplayGain, and metadata enrichment.

Recursively walks a directory, extracts metadata via mutagen, and applies:
- Metadata enrichment from YouTube Music (title, artist, album, cover art)
- Lyrics from lrclib.net (via lyrics.py)
- ReplayGain/R128 tags (via replaygain.py)
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from musicload.tagging_metadata import (
    _extract_partial_metadata,
    _parse_metadata_from_path,
    _search_metadata,
    _write_metadata_tags,
    extract_metadata,
)
from musicload.tagging_models import (
    FileMetadata,
    TagStats,
)
from musicload.tagging_models import (
    PartialMetadata as PartialMetadata,
)

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".opus", ".mp3", ".flac"}

# Map file extension to audio format name used by replaygain.py
EXTENSION_TO_FORMAT = {
    ".opus": "opus",
    ".mp3": "mp3",
    ".flac": "flac",
}


class NegativeResultCache:
    """Generic cache for failed lookups (enrichment, lyrics, etc.).

    Stores failed queries with timestamps so they aren't retried within the
    cooldown period. Corrupted files are backed up and reset.
    """

    def __init__(self, cache_path: Path, ttl_hours: int = 168):
        self._path = cache_path
        self._ttl_hours = ttl_hours
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = raw
        except Exception as e:
            logger.warning("Corrupted cache %s: %s — resetting", self._path, e)
            backup = self._path.with_suffix(".json.bak")
            try:
                self._path.rename(backup)
                logger.info("Backed up corrupted cache to %s", backup)
            except OSError:
                pass
            self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as e:
            logger.warning("Failed to save cache %s: %s", self._path.name, e)

    @staticmethod
    def make_key(artist: str, title: str) -> str:
        return f"{artist} - {title}".lower()

    def is_cached(self, key: str) -> bool:
        """Check if a failed lookup is still within the cooldown period."""
        if self._ttl_hours <= 0:
            return False
        entry = self._data.get(key)
        if entry is None:
            return False
        try:
            failed_at = datetime.fromisoformat(entry["failed_at"])
            elapsed_hours = (datetime.now(timezone.utc) - failed_at).total_seconds() / 3600
            return elapsed_hours < self._ttl_hours
        except (KeyError, ValueError):
            return False

    def record_failure(self, key: str) -> None:
        """Record a failed lookup."""
        self._data[key] = {"failed_at": datetime.now(timezone.utc).isoformat()}
        self._save()


def _make_enrichment_cache(data_dir: Path, ttl_hours: int) -> NegativeResultCache:
    return NegativeResultCache(data_dir / "enrichment_cache.json", ttl_hours)


# Backward-compatible alias
EnrichmentCache = NegativeResultCache


def collect_audio_files(directory: Path) -> list[Path]:
    """Recursively collect audio files with supported extensions."""
    files = []
    for ext in SUPPORTED_EXTENSIONS:
        files.extend(directory.rglob(f"*{ext}"))
    return sorted(files)


def _make_lyrics_cache_key(artist: str, title: str) -> str:
    """Create a synthetic video_id key for the lyrics cache in MetadataCache.

    Uses a 'tag:' prefix to avoid collisions with real YouTube video IDs.
    """
    return f"tag:{artist.lower()}|{title.lower()}"


def _has_cached_lyrics_result(
    cache_dir: Path | None,
    cache_key: str,
    cache_ttl: int,
    metadata: FileMetadata,
) -> bool:
    if not cache_dir:
        return False
    from musicload.metadata_cache import MetadataCache

    with MetadataCache(cache_dir) as cache:
        cached = cache.get_lyrics(cache_key, cache_ttl)
    if cached is None:
        return False
    result_type = "positive" if cached.lyrics is not None else "negative"
    logger.debug(
        "Lyrics cache hit (%s): %s - %s",
        result_type,
        metadata.artist,
        metadata.title,
    )
    return True


def _find_lyrics(metadata: FileMetadata) -> str | None:
    from musicload.lyrics import _search_lyrics, _try_cleaned_lookup, get_lyrics

    lyrics = get_lyrics(
        metadata.title, metadata.artist, metadata.duration_seconds
    )
    if not lyrics:
        lyrics = _search_lyrics(
            metadata.title,
            metadata.artist,
            metadata.album,
            metadata.duration_seconds,
        )
    if not lyrics:
        lyrics = _try_cleaned_lookup(
            metadata.title,
            metadata.artist,
            metadata.album,
            metadata.duration_seconds,
        )
    return lyrics


def _cache_lyrics_result(
    cache_dir: Path | None, cache_key: str, lyrics: str | None
) -> None:
    if not cache_dir:
        return
    import time

    from musicload.metadata_cache import CachedLyrics, MetadataCache

    with MetadataCache(cache_dir) as cache:
        cache.add_lyrics(
            CachedLyrics(
                video_id=cache_key,
                lyrics=lyrics,
                cached_at=time.time(),
            )
        )


def tag_file(
    file_path: Path,
    *,
    do_lyrics: bool = True,
    do_replaygain: bool = True,
    do_metadata: bool = False,
    root_dir: Path | None = None,
    enrichment_cache: NegativeResultCache | None = None,
    lyrics_cache_dir: Path | None = None,
    lyrics_cache_ttl: int = 168,
    dry_run: bool = False,
    stats: TagStats | None = None,
) -> None:
    """Apply metadata enrichment, lyrics, and/or ReplayGain tags to a single audio file."""
    if stats is None:
        stats = TagStats()

    # Phase 1: Metadata enrichment (runs before lyrics/replaygain)
    if do_metadata:
        _tag_metadata(
            file_path,
            root_dir=root_dir or file_path.parent,
            enrichment_cache=enrichment_cache,
            dry_run=dry_run,
            stats=stats,
        )

    # Phase 2: Extract metadata for lyrics/replaygain (re-read after enrichment)
    metadata = extract_metadata(file_path)
    if metadata is None and do_lyrics:
        # Fallback: parse metadata from filename for lyrics lookup.
        # This handles corrupted containers (e.g., bad Ogg pages) where mutagen
        # can't open the file but the audio is still playable.
        parsed_title, parsed_artist, parsed_album = _parse_metadata_from_path(
            file_path, root_dir or file_path.parent,
        )
        if parsed_title and parsed_artist:
            logger.info(
                "Using path-based metadata for %s: %s - %s",
                file_path.name, parsed_artist, parsed_title,
            )
            metadata = FileMetadata(
                title=parsed_title,
                artist=parsed_artist,
                album=parsed_album,
                duration_seconds=0,
            )
    if metadata is None:
        if do_lyrics or do_replaygain:
            logger.warning("Skipping %s: could not extract metadata", file_path.name)
            stats.errors += 1
        return

    if do_lyrics:
        _tag_lyrics(
            file_path, metadata,
            cache_dir=lyrics_cache_dir,
            cache_ttl=lyrics_cache_ttl,
            dry_run=dry_run, stats=stats,
        )

    if do_replaygain:
        _tag_replaygain(file_path, dry_run=dry_run, stats=stats)


def _tag_metadata(
    file_path: Path,
    *,
    root_dir: Path,
    enrichment_cache: EnrichmentCache | None,
    dry_run: bool,
    stats: TagStats,
) -> None:
    """Enrich missing metadata tags from YouTube Music search."""
    partial = _extract_partial_metadata(file_path)
    if partial is None:
        stats.metadata_failed += 1
        return

    # Determine what's missing
    needs_title = not partial.title
    needs_artist = not partial.artist
    needs_album = not partial.album
    needs_cover = not partial.has_cover

    if not (needs_title or needs_artist or needs_album or needs_cover):
        logger.debug("Metadata complete: %s", file_path.name)
        stats.metadata_skipped += 1
        return

    # Parse title/artist from filename for search query
    parsed_title, parsed_artist, parsed_album = _parse_metadata_from_path(file_path, root_dir)

    # Use existing tags if available, fall back to parsed values
    search_title = partial.title or parsed_title
    search_artist = partial.artist or parsed_artist

    if not search_title:
        logger.warning("Cannot determine title for enrichment: %s", file_path.name)
        stats.metadata_failed += 1
        return

    # Check enrichment cache for previous failures
    cache_key = NegativeResultCache.make_key(search_artist, search_title)
    if enrichment_cache and enrichment_cache.is_cached(cache_key):
        logger.debug("Enrichment cache hit (skipping): %s - %s", search_artist, search_title)
        stats.metadata_skipped += 1
        return

    if dry_run:
        logger.info(
            "[dry-run] Would enrich metadata for: %s (missing: %s)",
            file_path.name,
            ", ".join(
                f for f, needed in [
                    ("title", needs_title), ("artist", needs_artist),
                    ("album", needs_album), ("cover", needs_cover),
                ] if needed
            ),
        )
        return

    track = _search_metadata(search_title, search_artist)
    if track is None:
        logger.info("No YouTube Music result for: %s - %s", search_artist, search_title)
        if enrichment_cache:
            enrichment_cache.record_failure(cache_key)
        stats.metadata_failed += 1
        return

    if _write_metadata_tags(file_path, track, partial):
        logger.info("Enriched metadata: %s", file_path.name)
        stats.metadata_enriched += 1
    else:
        stats.metadata_skipped += 1


def _tag_lyrics(
    file_path: Path,
    metadata: FileMetadata,
    *,
    cache_dir: Path | None = None,
    cache_ttl: int = 168,
    dry_run: bool,
    stats: TagStats,
) -> None:
    """Fetch and save lyrics for a single file.

    Uses MetadataCache (the same SQLite cache as downloads) for negative result
    caching with a synthetic 'tag:artist|title' key.
    """
    from musicload.lyrics import save_lyrics

    lrc_path = file_path.with_suffix(".lrc")
    if lrc_path.exists():
        logger.info("Lyrics already exist: %s", lrc_path.name)
        stats.lyrics_skipped += 1
        return

    cache_key = _make_lyrics_cache_key(metadata.artist, metadata.title)
    if _has_cached_lyrics_result(cache_dir, cache_key, cache_ttl, metadata):
        stats.lyrics_skipped += 1
        return

    if dry_run:
        logger.info(
            "[dry-run] Would fetch lyrics for: %s - %s",
            metadata.artist,
            metadata.title,
        )
        return

    try:
        lyrics = _find_lyrics(metadata)
        if lyrics:
            save_lyrics(lyrics, file_path)
            stats.lyrics_added += 1
        else:
            logger.info("No lyrics found for: %s - %s", metadata.artist, metadata.title)
            stats.lyrics_not_found += 1

        _cache_lyrics_result(cache_dir, cache_key, lyrics)

    except Exception as e:
        logger.warning("Failed to fetch lyrics for %s: %s", file_path.name, e)
        stats.lyrics_failed += 1


def _has_replaygain_tags(file_path: Path, audio_format: str) -> bool:
    """Check if a file already has ReplayGain tags."""
    try:
        from mutagen import File

        audio = File(file_path)
        if audio is None:
            return False

        if audio_format == "opus":
            return (
                "R128_TRACK_GAIN" in audio
                or "REPLAYGAIN_TRACK_GAIN" in audio
            )
        elif audio_format == "mp3":
            replaygain_keys = [
                "REPLAYGAIN_TRACK_GAIN",
                "replaygain_track_gain",
            ]
            return any(key in audio for key in replaygain_keys)
        elif audio_format == "flac":
            replaygain_keys = [
                "REPLAYGAIN_TRACK_GAIN",
                "replaygain_track_gain",
            ]
            return any(key in audio for key in replaygain_keys)

        return False

    except Exception as e:
        logger.debug("Failed to check ReplayGain tags for %s: %s", file_path.name, e)
        return False


def _tag_replaygain(
    file_path: Path,
    *,
    dry_run: bool,
    stats: TagStats,
) -> None:
    """Apply ReplayGain tags to a single file."""
    from musicload.replaygain import apply_replaygain

    audio_format = EXTENSION_TO_FORMAT.get(file_path.suffix.lower())
    if not audio_format:
        logger.warning("Unknown format for ReplayGain: %s", file_path.suffix)
        stats.replaygain_failed += 1
        return

    if _has_replaygain_tags(file_path, audio_format):
        logger.info("ReplayGain tags already exist: %s", file_path.name)
        stats.replaygain_skipped += 1
        return

    if dry_run:
        logger.info("[dry-run] Would apply ReplayGain to: %s", file_path.name)
        return

    success = apply_replaygain(file_path, audio_format)
    if success:
        stats.replaygain_applied += 1
    else:
        stats.replaygain_failed += 1


def tag_directory(
    directory: Path,
    *,
    do_lyrics: bool = True,
    do_replaygain: bool = True,
    do_metadata: bool = False,
    dry_run: bool = False,
) -> TagStats:
    """Recursively tag all audio files in a directory."""
    files = collect_audio_files(directory)
    stats = TagStats(files_found=len(files))

    logger.info("Found %d audio files in %s", len(files), directory)

    # Resolve config for cache directories and TTL
    from musicload.config import get_config
    config = get_config()
    ttl_hours = config.lyrics_cache_hours

    enrichment_cache = _make_enrichment_cache(config.data_dir, ttl_hours) if do_metadata else None

    # Lyrics cache uses MetadataCache (SQLite) in data_dir
    lyrics_cache_dir = config.data_dir if do_lyrics else None

    for i, file_path in enumerate(files, 1):
        logger.info("[%d/%d] Processing: %s", i, len(files), file_path.name)
        try:
            tag_file(
                file_path,
                do_lyrics=do_lyrics,
                do_replaygain=do_replaygain,
                do_metadata=do_metadata,
                root_dir=directory,
                enrichment_cache=enrichment_cache,
                lyrics_cache_dir=lyrics_cache_dir,
                lyrics_cache_ttl=ttl_hours,
                dry_run=dry_run,
                stats=stats,
            )
        except Exception as e:
            logger.warning("Error processing %s: %s", file_path.name, e)
            stats.errors += 1

    return stats
