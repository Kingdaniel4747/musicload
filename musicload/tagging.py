"""Tag existing audio files with lyrics, ReplayGain, and metadata enrichment.

Recursively walks a directory, extracts metadata via mutagen, and applies:
- Metadata enrichment from YouTube Music (title, artist, album, cover art)
- Lyrics from lrclib.net (via lyrics.py)
- ReplayGain/R128 tags (via replaygain.py)
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".opus", ".mp3", ".flac"}

# Map file extension to audio format name used by replaygain.py
EXTENSION_TO_FORMAT = {
    ".opus": "opus",
    ".mp3": "mp3",
    ".flac": "flac",
}


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


def extract_metadata(file_path: Path) -> FileMetadata | None:
    """Extract title, artist, album, and duration from an audio file via mutagen.

    Returns FileMetadata if extraction succeeded, None if file is unreadable or has no metadata.
    """
    try:
        from mutagen import File

        audio = File(file_path)
        if audio is None:
            logger.debug("Mutagen could not open: %s", file_path)
            return None

        # Extract artist: prefer ARTISTS multi-value tag, fall back to artist
        artist_raw = (
            audio.get("ARTISTS")
            or audio.get("artists")
            or audio.get("artist")
        )
        title_raw = audio.get("title")

        if not title_raw or not artist_raw:
            logger.debug("Missing title or artist metadata in: %s", file_path)
            return None

        artist = artist_raw[0] if isinstance(artist_raw, list) else str(artist_raw)
        title = title_raw[0] if isinstance(title_raw, list) else str(title_raw)

        # Extract album (optional)
        album_raw = audio.get("album")
        album = None
        if album_raw:
            album = album_raw[0] if isinstance(album_raw, list) else str(album_raw)

        # Extract duration (mutagen stores as float seconds in info.length)
        duration_seconds = 0
        if audio.info and hasattr(audio.info, "length"):
            duration_seconds = int(audio.info.length)

        return FileMetadata(
            title=str(title),
            artist=str(artist),
            album=album,
            duration_seconds=duration_seconds,
        )

    except Exception as e:
        logger.warning("Failed to extract metadata from %s: %s", file_path, e)
        return None


def _extract_partial_metadata(file_path: Path) -> PartialMetadata | None:
    """Extract whatever metadata is available from an audio file.

    Unlike extract_metadata(), returns partial results even when title/artist is missing.
    Returns None only if mutagen can't open the file at all.
    """
    try:
        from mutagen import File

        audio = File(file_path)
        if audio is None:
            return None

        def _get_tag(key: str) -> str | None:
            raw = audio.get(key)
            if not raw:
                return None
            return str(raw[0]) if isinstance(raw, list) else str(raw)

        artist = _get_tag("ARTISTS") or _get_tag("artists") or _get_tag("artist")
        title = _get_tag("title")
        album = _get_tag("album")

        duration_seconds = 0
        if audio.info and hasattr(audio.info, "length"):
            duration_seconds = int(audio.info.length)

        has_cover = _has_cover_art(file_path)

        return PartialMetadata(
            title=title,
            artist=artist,
            album=album,
            duration_seconds=duration_seconds,
            has_cover=has_cover,
        )

    except Exception as e:
        logger.warning("Failed to extract partial metadata from %s: %s", file_path, e)
        return None


def _has_cover_art(file_path: Path) -> bool:
    """Check if an audio file already has embedded cover art."""
    try:
        from mutagen import File

        audio = File(file_path)
        if audio is None:
            return False

        suffix = file_path.suffix.lower()

        if suffix == ".flac":
            from mutagen.flac import FLAC
            flac = FLAC(file_path)
            return bool(flac.pictures)
        elif suffix == ".opus":
            return "metadata_block_picture" in audio
        elif suffix == ".mp3":
            from mutagen.id3 import ID3
            try:
                tags = ID3(file_path)
                return bool(tags.getall("APIC"))
            except Exception:
                return False

        return False
    except Exception as e:
        logger.debug("Failed to check cover art for %s: %s", file_path.name, e)
        return False


def _parse_metadata_from_path(
    file_path: Path,
    root_dir: Path,
) -> tuple[str, str, str | None]:
    """Parse title, artist, and album from the file path relative to root_dir.

    Handles:
      - Flat mode: "Artist - Title.opus" -> (title, artist, None)
      - Album mode: "Artist/Album/01 - Title.opus" -> (title, artist, album)
      - Fallback: "Title.opus" -> (title, "", None)
    """
    try:
        rel = file_path.relative_to(root_dir)
    except ValueError:
        rel = file_path

    parts = rel.parts
    stem = file_path.stem

    # Album mode: Artist/Album/TrackNum - Title.ext
    if len(parts) >= 3:
        artist = parts[-3] if len(parts) >= 3 else parts[0]
        album = parts[-2]
        title = stem
        # Strip leading track number pattern like "01 - "
        if " - " in title:
            title = title.split(" - ", 1)[1]
        return title, artist, album

    # Flat mode: Artist - Title.ext
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip(), artist.strip(), None

    return stem, "", None


def _search_metadata(title: str, artist: str):
    """Search YouTube Music for a track matching the given title and artist.

    Returns the top Track result or None.
    """
    from musicload.search import search

    query = f"{title} {artist}" if artist else title
    try:
        results = search(query, limit=1)
        return results[0] if results else None
    except Exception as e:
        logger.warning("YouTube Music search failed for '%s': %s", query, e)
        return None


def _write_metadata_tags(
    file_path: Path,
    track,
    partial: PartialMetadata,
) -> bool:
    """Write missing metadata tags to an audio file from a YouTube Music Track result.

    Only writes tags that are currently missing (doesn't overwrite existing).
    Returns True if any tags were written.
    """
    try:
        from mutagen import File

        audio = File(file_path)
        if audio is None:
            return False

        suffix = file_path.suffix.lower()
        wrote_any = False

        if suffix in (".opus", ".flac"):
            wrote_any = _write_vorbis_tags(file_path, audio, track, partial)
        elif suffix == ".mp3":
            wrote_any = _write_id3_tags(file_path, audio, track, partial)

        # Write multi-artist tags if track has multiple artists
        if track.artists and len(track.artists) > 1:
            from musicload.tags import write_multi_artist_tags
            write_multi_artist_tags(file_path, track.artists)
            wrote_any = True

        # Embed cover art if missing
        if not partial.has_cover and track.thumbnail_url:
            if _embed_cover_art(file_path, track.thumbnail_url):
                wrote_any = True

        return wrote_any

    except Exception as e:
        logger.warning("Failed to write metadata tags to %s: %s", file_path.name, e)
        return False


def _write_vorbis_tags(file_path: Path, audio, track, partial: PartialMetadata) -> bool:
    """Write missing Vorbis comment tags (Opus/FLAC)."""
    wrote = False
    if not partial.title and track.title:
        audio["title"] = [track.title]
        wrote = True
    if not partial.artist and track.artist:
        audio["artist"] = [track.artist]
        wrote = True
    if not partial.album and track.album:
        audio["album"] = [track.album]
        wrote = True
    if wrote:
        audio.save()
    return wrote


def _write_id3_tags(file_path: Path, audio, track, partial: PartialMetadata) -> bool:
    """Write missing ID3 tags (MP3)."""
    from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TIT2, TPE1

    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        tags = ID3()

    wrote = False
    if not partial.title and track.title:
        tags.add(TIT2(encoding=3, text=[track.title]))
        wrote = True
    if not partial.artist and track.artist:
        tags.add(TPE1(encoding=3, text=[track.artist]))
        wrote = True
    if not partial.album and track.album:
        tags.add(TALB(encoding=3, text=[track.album]))
        wrote = True
    if wrote:
        tags.save(file_path)
    return wrote


def _embed_cover_art(file_path: Path, thumbnail_url: str) -> bool:
    """Download thumbnail and embed as cover art."""
    import urllib.request

    try:
        with urllib.request.urlopen(thumbnail_url, timeout=15) as resp:  # noqa: S310
            image_data = resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")

        suffix = file_path.suffix.lower()

        if suffix == ".flac":
            return _embed_cover_flac(file_path, image_data, content_type)
        elif suffix == ".opus":
            return _embed_cover_opus(file_path, image_data, content_type)
        elif suffix == ".mp3":
            return _embed_cover_mp3(file_path, image_data, content_type)

        return False
    except Exception as e:
        logger.warning("Failed to embed cover art for %s: %s", file_path.name, e)
        return False


def _embed_cover_flac(file_path: Path, image_data: bytes, content_type: str) -> bool:
    from mutagen.flac import FLAC, Picture

    audio = FLAC(file_path)
    pic = Picture()
    pic.data = image_data
    pic.type = 3  # Cover (front)
    pic.mime = content_type
    audio.add_picture(pic)
    audio.save()
    return True


def _embed_cover_opus(file_path: Path, image_data: bytes, content_type: str) -> bool:
    import base64
    import struct

    from mutagen.oggopus import OggOpus

    audio = OggOpus(file_path)

    # Build a FLAC Picture block manually for metadata_block_picture
    # Format: type(4) + mime_len(4) + mime + desc_len(4) + desc + width(4) + height(4) + depth(4) + colors(4) + data_len(4) + data
    mime_bytes = content_type.encode("utf-8")
    desc_bytes = b""
    picture_block = struct.pack(
        ">II", 3, len(mime_bytes)  # type=Cover(front), mime length
    )
    picture_block += mime_bytes
    picture_block += struct.pack(">I", len(desc_bytes))  # description length
    picture_block += desc_bytes
    picture_block += struct.pack(">IIII", 0, 0, 0, 0)  # width, height, depth, colors
    picture_block += struct.pack(">I", len(image_data))  # data length
    picture_block += image_data

    audio["metadata_block_picture"] = [base64.b64encode(picture_block).decode("ascii")]
    audio.save()
    return True


def _embed_cover_mp3(file_path: Path, image_data: bytes, content_type: str) -> bool:
    from mutagen.id3 import APIC, ID3, ID3NoHeaderError

    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.add(APIC(
        encoding=3,
        mime=content_type,
        type=3,  # Cover (front)
        desc="Cover",
        data=image_data,
    ))
    tags.save(file_path)
    return True


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

    Uses MetadataCache (same SQLite cache as cron/download) for negative result
    caching with a synthetic 'tag:artist|title' key.
    """
    import time

    from musicload.lyrics import _search_lyrics, _try_cleaned_lookup, get_lyrics, save_lyrics
    from musicload.metadata_cache import CachedLyrics, MetadataCache

    lrc_path = file_path.with_suffix(".lrc")
    if lrc_path.exists():
        logger.info("Lyrics already exist: %s", lrc_path.name)
        stats.lyrics_skipped += 1
        return

    # Check lyrics cache (reuses MetadataCache from metadata_cache.py)
    cache_key = _make_lyrics_cache_key(metadata.artist, metadata.title)
    if cache_dir:
        with MetadataCache(cache_dir) as cache:
            cached = cache.get_lyrics(cache_key, cache_ttl)
            if cached is not None:
                if cached.lyrics is not None:
                    logger.debug("Lyrics cache hit (positive): %s - %s", metadata.artist, metadata.title)
                else:
                    logger.debug("Lyrics cache hit (negative): %s - %s", metadata.artist, metadata.title)
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
        # Strategy 1: exact match via get_lyrics
        lyrics = get_lyrics(metadata.title, metadata.artist, metadata.duration_seconds)

        # Strategy 2: search with album for fuzzy match
        if not lyrics:
            lyrics = _search_lyrics(
                metadata.title,
                metadata.artist,
                metadata.album,
                metadata.duration_seconds,
            )

        # Strategy 3: retry with cleaned title/artist (strip parentheticals, secondary artists)
        if not lyrics:
            lyrics = _try_cleaned_lookup(
                metadata.title,
                metadata.artist,
                metadata.album,
                metadata.duration_seconds,
            )

        if lyrics:
            save_lyrics(lyrics, file_path)
            stats.lyrics_added += 1
        else:
            logger.info("No lyrics found for: %s - %s", metadata.artist, metadata.title)
            stats.lyrics_not_found += 1

        # Cache result (positive or negative) in MetadataCache
        if cache_dir:
            with MetadataCache(cache_dir) as cache:
                cache.add_lyrics(CachedLyrics(
                    video_id=cache_key,
                    lyrics=lyrics,
                    cached_at=time.time(),
                ))

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
