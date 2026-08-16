"""YouTube Music search and explore functionality using ytmusicapi."""

import logging
from pathlib import Path

from ytmusicapi import YTMusic

from musicload.config import get_config
from musicload.explore import (
    get_charts as get_charts,
)
from musicload.explore import (
    get_mood_categories as get_mood_categories,
)
from musicload.explore import (
    get_mood_playlists as get_mood_playlists,
)
from musicload.explore import (
    get_new_releases as get_new_releases,
)
from musicload.metadata_cache import CachedSongMetadata, CachedTrack, MetadataCache
from musicload.models.search import (
    Album,
    ChartArtist,
    Charts,
    ChartTrack,
    MoodCategory,
    MoodPlaylist,
    MoodSection,
    SongMetadata,
    Track,
)
from musicload.search_utils import (
    ALLOWED_VIDEO_TYPES as ALLOWED_VIDEO_TYPES,
)
from musicload.search_utils import (
    VIDEO_TYPE_ATV as VIDEO_TYPE_ATV,
)
from musicload.search_utils import (
    VIDEO_TYPE_OFFICIAL_SOURCE as VIDEO_TYPE_OFFICIAL_SOURCE,
)
from musicload.search_utils import (
    VIDEO_TYPE_OMV as VIDEO_TYPE_OMV,
)
from musicload.search_utils import (
    VIDEO_TYPE_UGC as VIDEO_TYPE_UGC,
)
from musicload.search_utils import (
    is_allowed_video_type as is_allowed_video_type,
)
from musicload.search_utils import (
    parse_duration as _parse_duration,
)

logger = logging.getLogger(__name__)

# Models re-exported from musicload.models.search for backward compatibility:
# Track, Album, MoodCategory, MoodSection, MoodPlaylist, ChartTrack,
# ChartArtist, Charts, SongMetadata
__all__ = [
    "Track", "Album", "MoodCategory", "MoodSection", "MoodPlaylist",
    "ChartTrack", "ChartArtist", "Charts", "SongMetadata",
]


def search(query: str, limit: int = 20) -> list[Track]:
    """
    Search YouTube Music for tracks.

    Args:
        query: Search query string
        limit: Maximum number of results to return

    Returns:
        List of Track objects matching the query

    Raises:
        Exception: If YouTube Music API fails (e.g., JSONDecodeError, network error)
    """
    yt = YTMusic()
    try:
        results = yt.search(query, filter="songs", limit=limit)
    except Exception as e:
        logger.error("YouTube Music search failed for query '%s': %s", query, e)
        raise

    tracks = []
    for item in results:
        if item.get("resultType") != "song":
            continue

        # Skip results that are actually music videos, not the studio song version.
        # ATV = official audio track (has proper album metadata).
        # OMV/UGC/OFFICIAL_SOURCE = real music video / user upload -> we don't want these.
        video_type = item.get("videoType")
        if video_type is not None and video_type != VIDEO_TYPE_ATV:
            logger.info(
                "Skipping non-ATV search result '%s' (videoType=%s)",
                item.get("title"),
                video_type,
            )
            continue

        # Extract artist name(s) - keep full list for multi-value tags
        artist_objects = item.get("artists", [])
        artist_names = [a["name"] for a in artist_objects] if artist_objects else ["Unknown Artist"]
        artist_name = artist_names[0]  # Primary artist for display/compatibility

        # Extract album name
        album = item.get("album")
        album_name = album["name"] if album else None

        # Extract duration in seconds
        duration_text = item.get("duration", "0:00")
        duration_seconds = _parse_duration(duration_text)

        # Extract thumbnail URL (prefer larger size)
        thumbnails = item.get("thumbnails", [])
        thumbnail_url = thumbnails[-1]["url"] if thumbnails else None

        # Extract view count (formatted string like "1.9B", "47M", etc.)
        view_count = item.get("views")

        tracks.append(
            Track(
                video_id=item["videoId"],
                title=item.get("title", "Unknown Title"),
                artist=artist_name,
                artists=artist_names,
                album=album_name,
                duration_seconds=duration_seconds,
                thumbnail_url=thumbnail_url,
                view_count=view_count,
                video_type=video_type,
            )
        )

    logger.info("Found %d tracks for query: %s", len(tracks), query)
    return tracks


def search_albums(query: str, limit: int = 20) -> list[Album]:
    """
    Search YouTube Music for albums.

    Args:
        query: Search query string
        limit: Maximum number of results to return

    Returns:
        List of Album objects matching the query

    Raises:
        Exception: If YouTube Music API fails (e.g., JSONDecodeError, network error)
    """
    yt = YTMusic()
    try:
        results = yt.search(query, filter="albums", limit=limit)
    except Exception as e:
        logger.error("YouTube Music album search failed for query '%s': %s", query, e)
        raise

    albums = []
    for item in results:
        if item.get("resultType") != "album":
            continue

        # Extract artist name(s)
        artists = item.get("artists", [])
        artist_name = artists[0]["name"] if artists else "Unknown Artist"

        # Extract year
        year_str = item.get("year")
        year = int(year_str) if year_str else None

        # Extract track count
        track_count = item.get("trackCount")

        # Extract thumbnail URL (prefer larger size)
        thumbnails = item.get("thumbnails", [])
        thumbnail_url = thumbnails[-1]["url"] if thumbnails else None

        albums.append(
            Album(
                browse_id=item["browseId"],
                title=item.get("title", "Unknown Album"),
                artist=artist_name,
                year=year,
                track_count=track_count,
                thumbnail_url=thumbnail_url,
            )
        )

    logger.info("Found %d albums for query: %s", len(albums), query)
    return albums


def get_album_tracks(browse_id: str) -> list[Track]:
    """
    Get all tracks for an album.

    Args:
        browse_id: YouTube Music album browse ID

    Returns:
        List of Track objects from the album

    Raises:
        Exception: If YouTube Music API fails (e.g., JSONDecodeError, network error)
    """
    yt = YTMusic()
    try:
        album_info = yt.get_album(browse_id)
    except Exception as e:
        logger.error("YouTube Music get_album failed for browse_id '%s': %s", browse_id, e)
        raise

    tracks = []
    for item in album_info.get("tracks", []):
        # Extract artist name(s) - keep full list for multi-value tags
        artist_objects = item.get("artists", [])
        artist_names = [a["name"] for a in artist_objects] if artist_objects else ["Unknown Artist"]
        artist_name = artist_names[0]  # Primary artist for display/compatibility

        # Extract duration in seconds
        duration_text = item.get("duration", "0:00")
        duration_seconds = _parse_duration(duration_text)

        # Extract thumbnail URL from album info (prefer larger size)
        thumbnails = album_info.get("thumbnails", [])
        thumbnail_url = thumbnails[-1]["url"] if thumbnails else None

        tracks.append(
            Track(
                video_id=item["videoId"],
                title=item.get("title", "Unknown Title"),
                artist=artist_name,
                artists=artist_names,
                album=album_info.get("title"),
                duration_seconds=duration_seconds,
                thumbnail_url=thumbnail_url,
                view_count=None,
            )
        )

    logger.info("Found %d tracks in album: %s", len(tracks), album_info.get("title"))
    return tracks


def get_playlist_tracks(playlist_id: str, allow_ugc: bool = False) -> list[Track]:
    """Get tracks from a YouTube Music playlist.

    Radio playlists (IDs starting with 'RDAM') are not supported because they
    use a different API structure and cannot be fetched via get_playlist().

    Args:
        playlist_id: YouTube Music playlist ID
        allow_ugc: If True, include UGC and OFFICIAL_SOURCE_MUSIC tracks.
            By default, only ATV and OMV tracks are included.

    Returns:
        List of Track objects from the playlist.

    Raises:
        ValueError: If the playlist_id is a radio playlist (starts with 'RDAM')
        Exception: If YouTube Music API fails
    """
    # Check for radio playlists upfront
    if playlist_id.startswith('RDAM'):
        raise ValueError(
            f"Radio playlists are not supported. Playlist ID: {playlist_id}. "
            "Radio playlists use a different API structure and cannot be fetched."
        )

    yt = YTMusic()
    try:
        raw = yt.get_playlist(playlist_id)
    except Exception as e:
        logger.error("YouTube Music get_playlist failed for playlist_id '%s': %s", playlist_id, e)
        raise ValueError(
            f"Playlist '{playlist_id}' is unavailable or could not be loaded"
        ) from e

    tracks = []
    skipped_count = 0
    for item in raw.get("tracks", []):
        video_id = item.get("videoId")
        if not video_id:
            continue

        video_type = item.get("videoType")

        # Filter by video type when available
        if video_type is not None and not is_allowed_video_type(video_type, allow_ugc):
            logger.debug(
                "Skipping track '%s' (%s): video_type=%s",
                item.get("title", "?"),
                video_id,
                video_type,
            )
            skipped_count += 1
            continue

        artist_objects = item.get("artists", [])
        artist_names = [a["name"] for a in artist_objects] if artist_objects else ["Unknown Artist"]
        artist_name = artist_names[0]

        album_obj = item.get("album")
        album_name = album_obj.get("name") if isinstance(album_obj, dict) else None

        duration_text = item.get("duration", "0:00")
        duration_seconds = item.get("duration_seconds") or _parse_duration(duration_text)

        thumbnails = item.get("thumbnails", [])
        thumbnail_url = thumbnails[-1]["url"] if thumbnails else None

        tracks.append(
            Track(
                video_id=video_id,
                title=item.get("title", "Unknown Title"),
                artist=artist_name,
                artists=artist_names,
                album=album_name,
                duration_seconds=duration_seconds,
                thumbnail_url=thumbnail_url,
                view_count=None,
                video_type=video_type,
            )
        )

    if skipped_count:
        logger.info(
            "Filtered %d non-official tracks from playlist: %s",
            skipped_count,
            raw.get("title", playlist_id),
        )
    logger.info("Found %d tracks in playlist: %s", len(tracks), raw.get("title", playlist_id))
    return tracks


def get_song_metadata(video_id: str) -> SongMetadata | None:
    """Fetch clean song metadata from YouTube Music via get_song().

    This returns metadata directly from YouTube Music's database, which
    is more accurate than yt-dlp's metadata extracted from video titles.
    The clean metadata significantly improves lyrics lookup success rates
    on lrclib.net.

    Results are cached in SQLite (per video_id) to avoid redundant API calls.

    Error Handling Strategy:
        This function is used for optional metadata enhancement. Errors are
        caught and logged, returning None to allow the caller to continue with
        fallback metadata instead of failing the entire operation.

    Args:
        video_id: YouTube video ID

    Returns:
        SongMetadata with clean title/artist/album/duration, or None if fetch fails.
    """
    config = get_config()
    cache_dir = config.data_dir

    # Check cache first
    with MetadataCache(cache_dir) as cache:
        cached = cache.get_song_metadata(video_id)
        if cached is not None:
            logger.debug("Cache hit for song metadata: %s", video_id)
            return SongMetadata(
                title=cached.title,
                artist=cached.artist,
                album=cached.album,
                duration_seconds=cached.duration_seconds,
            )

    yt = YTMusic()
    try:
        song_data = yt.get_song(video_id)
    except Exception as e:
        logger.warning("Failed to fetch song metadata for video_id '%s': %s", video_id, e)
        return None

    video_details = song_data.get("videoDetails", {})
    title = video_details.get("title")
    author = video_details.get("author")
    length_seconds_str = video_details.get("lengthSeconds", "0")

    if not title or not author:
        logger.debug("Incomplete videoDetails for video_id '%s', trying watch playlist", video_id)
        result = _get_metadata_from_watch_playlist(yt, video_id)
        if result is not None:
            _cache_song_metadata(cache_dir, video_id, result)
        return result

    if not length_seconds_str or not length_seconds_str.isdigit():
        logger.warning("Invalid duration for video %s", video_id)
        return None
    duration_seconds = int(length_seconds_str)

    # videoDetails does not include album; try watch playlist for album info
    album = _get_album_from_watch_playlist(yt, video_id)

    logger.debug(
        "Got song metadata for '%s': title='%s', artist='%s', album='%s', duration=%ds",
        video_id, title, author, album, duration_seconds,
    )

    result = SongMetadata(
        title=title,
        artist=author,
        album=album,
        duration_seconds=duration_seconds,
    )
    _cache_song_metadata(cache_dir, video_id, result)
    return result


def _cache_song_metadata(cache_dir: Path, video_id: str, metadata: SongMetadata) -> None:
    """Store song metadata in the cache (errors swallowed)."""
    with MetadataCache(cache_dir) as cache:
        cache.add_song_metadata(
            CachedSongMetadata(
                video_id=video_id,
                title=metadata.title,
                artist=metadata.artist,
                album=metadata.album,
                duration_seconds=metadata.duration_seconds,
            )
        )


def _get_album_from_watch_playlist(yt: YTMusic, video_id: str) -> str | None:
    """Extract album name from watch playlist data.

    The get_song() endpoint does not include album info in videoDetails,
    but get_watch_playlist() returns it per track.

    Error Handling Strategy:
        Errors are caught and logged at debug level, returning None. This is
        internal helper for optional album metadata - failures should not break
        the metadata fetch operation.

    Args:
        yt: YTMusic instance (reused to avoid re-initialization)
        video_id: YouTube video ID

    Returns:
        Album name string, or None if not available.
    """
    try:
        watch_data = yt.get_watch_playlist(videoId=video_id, limit=1)
        tracks = watch_data.get("tracks", [])
        if tracks:
            album_obj = tracks[0].get("album")
            if isinstance(album_obj, dict):
                return album_obj.get("name")
    except Exception as e:
        logger.debug("Failed to get album from watch playlist for '%s': %s", video_id, e)
    return None


def _get_metadata_from_watch_playlist(yt: YTMusic, video_id: str) -> SongMetadata | None:
    """Fallback: extract full metadata from watch playlist.

    Used when get_song() returns incomplete videoDetails.

    Error Handling Strategy:
        Errors are caught and logged, returning None. This is a fallback helper
        for optional metadata enhancement - failures allow the caller to proceed
        with yt-dlp metadata instead of blocking the download operation.

    Args:
        yt: YTMusic instance (reused to avoid re-initialization)
        video_id: YouTube video ID

    Returns:
        SongMetadata from watch playlist data, or None if fetch fails.
    """
    try:
        watch_data = yt.get_watch_playlist(videoId=video_id, limit=1)
        tracks = watch_data.get("tracks", [])
        if not tracks:
            return None

        track = tracks[0]
        title = track.get("title")
        if not title:
            return None

        artist_objects = track.get("artists", [])
        artist = artist_objects[0]["name"] if artist_objects else None
        if not artist:
            return None

        album_obj = track.get("album")
        album = album_obj.get("name") if isinstance(album_obj, dict) else None

        length_text = track.get("length", "0:00")
        duration_seconds = _parse_duration(length_text)

        logger.debug(
            "Got metadata from watch playlist for '%s': title='%s', artist='%s', album='%s'",
            video_id, title, artist, album,
        )

        return SongMetadata(
            title=title,
            artist=artist,
            album=album,
            duration_seconds=duration_seconds,
        )
    except Exception as e:
        logger.warning("Failed to get metadata from watch playlist for '%s': %s", video_id, e)
        return None


def _format_view_count(views: int) -> str:
    """Format view count as '1.9B', '47M', '1.5K', etc."""
    if views >= 1_000_000_000:
        return f"{views / 1_000_000_000:.1f}B"
    elif views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M"
    elif views >= 1_000:
        return f"{views / 1_000:.1f}K"
    else:
        return str(views)


_YOUTUBE_HOSTS = frozenset(
    {"music.youtube.com", "www.youtube.com", "youtube.com", "youtu.be"}
)


def _video_url_result(video_id: str) -> dict[str, str] | None:
    if len(video_id) != 11:
        return None
    return {"type": "video", "id": video_id}


def _playlist_url_result(
    playlist_id: str, query_params: dict[str, list[str]]
) -> dict[str, str]:
    if not playlist_id.startswith("RDAM"):
        return {"type": "playlist", "id": playlist_id}

    video_id = query_params.get("v", [""])[0]
    video_result = _video_url_result(video_id)
    if video_result:
        logger.info(
            "Radio playlist detected in URL, falling back to video: %s", video_id
        )
        return video_result
    logger.warning("Radio playlist URLs are not supported: %s", playlist_id)
    return {"type": "unsupported_radio", "id": playlist_id}


def parse_youtube_url(url: str) -> dict[str, str] | None:
    """
    Parse YouTube/YouTube Music URL and extract video_id or playlist_id.

    Radio playlists (IDs starting with 'RDAM') are not supported because they
    use a different API structure. For URLs containing both a video and a radio
    playlist, the video is returned instead.

    Args:
        url: Full URL string

    Returns:
        Dictionary with 'type' and 'id' keys, or None if not a valid YouTube URL.
        Example: {'type': 'video', 'id': 'dQw4w9WgXcQ'}
                 {'type': 'playlist', 'id': 'PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf'}
                 {'type': 'unsupported_radio', 'id': 'RDAMVMkX_n5Knuce4'}
    """
    from urllib.parse import parse_qs, urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if parsed.netloc not in _YOUTUBE_HOSTS:
        return None

    if parsed.netloc == "youtu.be":
        return _video_url_result(parsed.path.lstrip("/"))

    query_params = parse_qs(parsed.query)
    if "list" in query_params:
        return _playlist_url_result(query_params["list"][0], query_params)
    return _video_url_result(query_params.get("v", [""])[0])


def get_track_from_video_id(video_id: str) -> Track:
    """
    Get complete track metadata from a YouTube video ID.

    Uses ytmusicapi's get_song() to retrieve structured metadata including
    title, artist, thumbnail, view count, and duration.

    Results are cached in SQLite (per video_id) to avoid redundant API calls.

    Args:
        video_id: YouTube video ID (11 characters)

    Returns:
        Track object with full metadata

    Raises:
        Exception: If video is unavailable or API fails
    """
    config = get_config()
    cache_dir = config.data_dir

    # Check cache first
    with MetadataCache(cache_dir) as cache:
        cached = cache.get_track(video_id)
        if cached is not None:
            logger.debug("Cache hit for track: %s", video_id)
            return Track(
                video_id=cached.video_id,
                title=cached.title,
                artist=cached.artist,
                artists=cached.artists,
                album=cached.album,
                duration_seconds=cached.duration_seconds,
                thumbnail_url=cached.thumbnail_url,
                view_count=cached.view_count,
                video_type=cached.video_type,
            )

    yt = YTMusic()
    try:
        song_data = yt.get_song(video_id)
    except Exception as e:
        logger.error("Failed to fetch track for video_id '%s': %s", video_id, e)
        raise

    video_details = song_data.get("videoDetails", {})
    if not video_details:
        raise ValueError(f"No video details found for video_id: {video_id}")

    # Extract fields
    title = video_details.get("title", "Unknown Title")
    artist = video_details.get("author", "Unknown Artist")
    length_seconds = int(video_details.get("lengthSeconds", "0"))

    # Extract thumbnail (prefer largest)
    thumbnails = video_details.get("thumbnail", {}).get("thumbnails", [])
    thumbnail_url = thumbnails[-1]["url"] if thumbnails else None

    # Extract and format view count
    view_count_raw = video_details.get("viewCount")
    view_count = _format_view_count(int(view_count_raw)) if view_count_raw else None

    # Extract video type (musicVideoType in videoDetails)
    video_type = video_details.get("musicVideoType")

    logger.info("Fetched track from URL: %s - %s", artist, title)

    track = Track(
        video_id=video_id,
        title=title,
        artist=artist,
        artists=[artist],
        album=None,  # get_song doesn't include album info
        duration_seconds=length_seconds,
        thumbnail_url=thumbnail_url,
        view_count=view_count,
        video_type=video_type,
    )

    # Cache the result
    with MetadataCache(cache_dir) as cache:
        cache.add_track(
            CachedTrack(
                video_id=track.video_id,
                title=track.title,
                artist=track.artist,
                artists=track.artists,
                album=track.album,
                duration_seconds=track.duration_seconds,
                thumbnail_url=track.thumbnail_url,
                view_count=track.view_count,
                video_type=track.video_type,
            )
        )

    return track
