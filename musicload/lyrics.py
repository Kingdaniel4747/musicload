"""Lyrics fetching from lrclib.net.

Supports two lookup strategies:
1. Enhanced: Fetches clean metadata from YouTube Music API (ytmusicapi) for accurate
   title/artist/album/duration, then uses lrclib.net /api/get (exact match) followed
   by /api/search (fuzzy match) as fallback.
2. Basic: Uses yt-dlp-derived metadata (from video title/filepath) with /api/get only.

The enhanced strategy is used when a video_id is provided to get_lyrics_for_video().
The basic strategy is the original get_lyrics() behavior, preserved as fallback.
"""

import logging
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

LRCLIB_BASE_URL = "https://lrclib.net/api"

# Matches trailing (content) or [content] at end of string
_TRAILING_PARENS_RE = re.compile(r"\s*(?:\([^)]+\)|\[[^\]]+\])\s*$")

# Splits multi-artist strings on comma, semicolon, feat., ft., featuring
_ARTIST_SPLIT_RE = re.compile(
    r"\s*[,;]\s*|\s+(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE
)

# Splits artist collaboration prefixes on x, &, comma, semicolon, feat/ft
_COLLAB_SPLIT_RE = re.compile(
    r"\s+x\s+|\s*[,;&]\s*|\s+(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE
)


def _clean_title(title: str) -> str:
    """Strip trailing parenthetical/bracketed suffixes from a track title.

    Iteratively removes patterns like (Radio Edit), [Official Video], (feat. X).
    Returns the original title if cleaning would produce an empty string.
    """
    cleaned = title
    while True:
        new = _TRAILING_PARENS_RE.sub("", cleaned).strip()
        if new == cleaned or not new:
            break
        cleaned = new
    return cleaned if cleaned else title


def _clean_artist(artist: str) -> str:
    """Extract primary artist name from a multi-artist string.

    Splits on commas, semicolons, and feat/ft/featuring markers.
    Returns the original artist if cleaning would produce an empty string.
    """
    parts = _ARTIST_SPLIT_RE.split(artist, maxsplit=1)
    primary = parts[0].strip()
    return primary if primary else artist


def _strip_artist_from_title(title: str, artist: str) -> tuple[str, str] | None:
    """Strip artist name prefix from a title like "Artist1 x Artist2 - Song Title".

    Many YouTube video titles embed the artist names before a dash separator.
    When the known artist appears in the prefix, extract the real song title
    and combine all prefix artists into a proper artist string.

    Returns (clean_title, combined_artists) if the prefix contains the known artist,
    or None if no artist prefix was detected.
    """
    if " - " not in title:
        return None

    prefix, suffix = title.split(" - ", 1)
    suffix = suffix.strip()
    if not suffix:
        return None

    # Check if known artist appears in the prefix (case-insensitive)
    if artist.lower() not in prefix.lower():
        return None

    # Extract all artists from the prefix
    prefix_artists = [a.strip() for a in _COLLAB_SPLIT_RE.split(prefix) if a.strip()]
    combined = ", ".join(prefix_artists) if prefix_artists else artist

    return suffix, combined


def _try_cleaned_lookup(
    title: str,
    artist: str,
    album: str | None,
    duration_seconds: int,
) -> str | None:
    """Try lyrics lookup with cleaned title and artist as fallback.

    Only makes API calls if cleaning actually changed the title or artist.
    Tries exact match first, then search.
    """
    cleaned_title = _clean_title(title)
    cleaned_artist = _clean_artist(artist)

    if cleaned_title != title or cleaned_artist != artist:
        logger.info(
            "Retrying lyrics with cleaned metadata: '%s' by '%s' (was: '%s' by '%s')",
            cleaned_title,
            cleaned_artist,
            title,
            artist,
        )

        lyrics = _get_lyrics_exact(cleaned_title, cleaned_artist, duration_seconds)
        if lyrics:
            return lyrics

        lyrics = _search_lyrics(cleaned_title, cleaned_artist, album, duration_seconds)
        if lyrics:
            return lyrics

    # Try stripping artist prefix from title (e.g., "Artist x Artist2 - Song")
    stripped = _strip_artist_from_title(cleaned_title, cleaned_artist)
    if stripped:
        stripped_title, stripped_artist = stripped
        logger.info(
            "Retrying lyrics with artist-stripped title: '%s' by '%s' (was: '%s' by '%s')",
            stripped_title,
            stripped_artist,
            title,
            artist,
        )
        lyrics = _get_lyrics_exact(stripped_title, stripped_artist, duration_seconds)
        if lyrics:
            return lyrics

        lyrics = _search_lyrics(stripped_title, stripped_artist, album, duration_seconds)
        if lyrics:
            return lyrics

    # Nothing worked
    if cleaned_title == title and cleaned_artist == artist and stripped is None:
        return None  # Nothing changed at all, signal skip

    return None


def get_lyrics_for_video(
    video_id: str,
    fallback_title: str,
    fallback_artist: str,
    fallback_duration: int,
) -> str | None:
    """Fetch lyrics using ytmusicapi metadata with yt-dlp fallback.

    This is the primary lyrics lookup function. It first fetches clean metadata
    from YouTube Music API (which has accurate title/artist/album info), then
    attempts multiple lrclib.net lookup strategies:

    1. Exact match (/api/get) with ytmusicapi metadata
    2. Search (/api/search) with ytmusicapi metadata (if exact match fails)
    3. Exact match (/api/get) with yt-dlp fallback metadata (if ytmusicapi fails)

    Results are cached in SQLite (both positive and negative) to avoid redundant
    lrclib.net API calls on subsequent syncs.

    Args:
        video_id: YouTube video ID for fetching clean metadata
        fallback_title: Track title from yt-dlp (used as last resort)
        fallback_artist: Artist name from yt-dlp (used as last resort)
        fallback_duration: Duration in seconds from yt-dlp (used as last resort)

    Returns:
        LRC formatted lyrics string, or None if not found
    """
    # Import here to avoid circular dependency
    from musicload.config import get_config
    from musicload.metadata_cache import CachedLyrics, MetadataCache
    from musicload.search import get_song_metadata

    config = get_config()
    cache_dir = config.data_dir

    # Check cache first
    with MetadataCache(cache_dir) as cache:
        cached = cache.get_lyrics(video_id, config.lyrics_cache_hours)
        if cached is not None:
            if cached.lyrics is not None:
                logger.info("Lyrics cache hit (positive) for video_id '%s'", video_id)
            else:
                logger.info("Lyrics cache hit (negative) for video_id '%s'", video_id)
            return cached.lyrics

    # Cache miss — do the full lookup
    lyrics = _lookup_lyrics_for_video(video_id, fallback_title, fallback_artist, fallback_duration)

    # Store result in cache (positive or negative)
    with MetadataCache(cache_dir) as cache:
        cache.add_lyrics(CachedLyrics(
            video_id=video_id,
            lyrics=lyrics,
            cached_at=time.time(),
        ))

    return lyrics


def _lookup_lyrics_for_video(
    video_id: str,
    fallback_title: str,
    fallback_artist: str,
    fallback_duration: int,
) -> str | None:
    """Internal lyrics lookup logic (uncached).

    Extracted from get_lyrics_for_video() so the cache wrapper is clean.
    """
    from musicload.search import get_song_metadata

    # Step 1: Try to get clean metadata from ytmusicapi
    metadata = get_song_metadata(video_id)

    if metadata:
        logger.info(
            "Using ytmusicapi metadata for lyrics lookup: '%s' by '%s' (album: %s, duration: %ds)",
            metadata.title, metadata.artist, metadata.album, metadata.duration_seconds,
        )

        # Step 2: Try exact match with ytmusicapi metadata
        lyrics = _get_lyrics_exact(
            metadata.title, metadata.artist, metadata.duration_seconds
        )
        if lyrics:
            return lyrics

        # Step 3: Try search with ytmusicapi metadata (includes album for better matching)
        lyrics = _search_lyrics(
            metadata.title, metadata.artist, metadata.album, metadata.duration_seconds
        )
        if lyrics:
            return lyrics

        # Step 4: Try with cleaned ytmusicapi metadata (strip parentheticals, secondary artists)
        lyrics = _try_cleaned_lookup(
            metadata.title, metadata.artist, metadata.album, metadata.duration_seconds
        )
        if lyrics:
            return lyrics

        logger.info(
            "Lyrics not found with ytmusicapi metadata, trying yt-dlp fallback for: %s - %s",
            metadata.artist, metadata.title,
        )

    else:
        logger.info(
            "Could not fetch ytmusicapi metadata for video_id '%s', using yt-dlp metadata",
            video_id,
        )

    # Step 5: Fall back to yt-dlp metadata with exact match (original behavior)
    lyrics = _get_lyrics_exact(fallback_title, fallback_artist, fallback_duration)
    if lyrics:
        return lyrics

    # Step 6: Try with cleaned yt-dlp fallback metadata
    return _try_cleaned_lookup(fallback_title, fallback_artist, None, fallback_duration)


def get_lyrics(track_name: str, artist_name: str, duration_seconds: int) -> str | None:
    """Fetch synced lyrics from lrclib.net using exact match.

    This is the original lyrics lookup function. It uses the /api/get endpoint
    which requires exact track_name, artist_name, and duration match.

    Kept for backward compatibility. Prefer get_lyrics_for_video() when a
    video_id is available.

    Args:
        track_name: Song title
        artist_name: Artist name
        duration_seconds: Track duration in seconds

    Returns:
        LRC formatted lyrics string, or None if not found
    """
    return _get_lyrics_exact(track_name, artist_name, duration_seconds)


def _get_lyrics_exact(track_name: str, artist_name: str, duration_seconds: int) -> str | None:
    """Fetch lyrics from lrclib.net /api/get endpoint (exact match).

    Args:
        track_name: Song title
        artist_name: Artist name
        duration_seconds: Track duration in seconds

    Returns:
        LRC formatted lyrics string, or None if not found
    """
    if duration_seconds <= 0:
        logger.debug("Skipping exact lyrics lookup (no duration): %s - %s", artist_name, track_name)
        return None
    try:
        response = httpx.get(
            f"{LRCLIB_BASE_URL}/get",
            params={
                "track_name": track_name,
                "artist_name": artist_name,
                "duration": duration_seconds,
            },
            timeout=10.0,
        )

        if response.status_code == 404:
            logger.debug("No exact lyrics match for: %s - %s (duration: %ds)", artist_name, track_name, duration_seconds)
            return None

        response.raise_for_status()
        data = response.json()

        return _extract_lyrics_from_response(data, artist_name, track_name)

    except httpx.HTTPError as e:
        logger.warning("Failed to fetch lyrics (exact): %s", e)
        return None


def _search_lyrics(
    track_name: str,
    artist_name: str,
    album_name: str | None,
    duration_seconds: int,
) -> str | None:
    """Search for lyrics using lrclib.net /api/search endpoint (fuzzy match).

    The search endpoint is more forgiving than /api/get and can find lyrics
    even when title/artist don't match exactly. Results are filtered by
    duration to avoid false positives.

    Args:
        track_name: Song title
        artist_name: Artist name
        album_name: Album name (optional, improves matching)
        duration_seconds: Track duration in seconds (used to filter results)

    Returns:
        LRC formatted lyrics string, or None if not found
    """
    try:
        params: dict[str, str] = {
            "track_name": track_name,
            "artist_name": artist_name,
        }
        if album_name:
            params["album_name"] = album_name

        response = httpx.get(
            f"{LRCLIB_BASE_URL}/search",
            params=params,
            timeout=10.0,
        )

        if response.status_code == 404:
            logger.debug("No search results for: %s - %s", artist_name, track_name)
            return None

        response.raise_for_status()
        results = response.json()

        if not isinstance(results, list) or not results:
            logger.debug("Empty search results for: %s - %s", artist_name, track_name)
            return None

        # Filter results by duration (allow 3 second tolerance)
        duration_tolerance = 3
        matching_results = [
            r for r in results
            if abs(r.get("duration", 0) - duration_seconds) <= duration_tolerance
        ]

        # If duration filtering removes all results, use the full result set
        # (better to return approximate lyrics than nothing)
        candidates = matching_results if matching_results else results

        # Pick the best result: prefer synced lyrics, then plain lyrics
        for result in candidates:
            synced = result.get("syncedLyrics")
            if synced:
                logger.info(
                    "Found synced lyrics via search for: %s - %s (from: %s - %s)",
                    artist_name, track_name,
                    result.get("artistName", "?"), result.get("trackName", "?"),
                )
                return synced

        # No synced lyrics found, try plain lyrics
        for result in candidates:
            plain = result.get("plainLyrics")
            if plain:
                logger.info(
                    "Found plain lyrics via search for: %s - %s (from: %s - %s)",
                    artist_name, track_name,
                    result.get("artistName", "?"), result.get("trackName", "?"),
                )
                return plain

        logger.debug("Search returned results but no lyrics for: %s - %s", artist_name, track_name)
        return None

    except httpx.HTTPError as e:
        logger.warning("Failed to search lyrics: %s", e)
        return None


def _extract_lyrics_from_response(data: dict, artist_name: str, track_name: str) -> str | None:
    """Extract lyrics from a lrclib.net API response object.

    Prefers synced lyrics over plain lyrics.

    Args:
        data: Response JSON from lrclib.net
        artist_name: Artist name (for logging)
        track_name: Track name (for logging)

    Returns:
        LRC formatted lyrics string, or None if response has no lyrics
    """
    synced = data.get("syncedLyrics")
    if synced:
        logger.info("Found synced lyrics for: %s - %s", artist_name, track_name)
        return synced

    plain = data.get("plainLyrics")
    if plain:
        logger.info("Found plain lyrics for: %s - %s", artist_name, track_name)
        return plain

    return None


def save_lyrics(lyrics: str, audio_path: Path) -> Path:
    """Save lyrics as an LRC file alongside the audio file.

    Args:
        lyrics: LRC formatted lyrics string
        audio_path: Path to the audio file

    Returns:
        Path to the saved LRC file
    """
    lrc_path = audio_path.with_suffix(".lrc")
    lrc_path.write_text(lyrics, encoding="utf-8")
    logger.info("Saved lyrics to: %s", lrc_path)
    return lrc_path
