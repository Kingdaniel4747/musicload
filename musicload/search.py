"""YouTube Music search and explore functionality using ytmusicapi."""

import logging
from pathlib import Path

from ytmusicapi import YTMusic

from musicload.config import get_config
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

logger = logging.getLogger(__name__)

# Video type constants from YouTube Music API
VIDEO_TYPE_ATV = "MUSIC_VIDEO_TYPE_ATV"
VIDEO_TYPE_OMV = "MUSIC_VIDEO_TYPE_OMV"
VIDEO_TYPE_UGC = "MUSIC_VIDEO_TYPE_UGC"
VIDEO_TYPE_OFFICIAL_SOURCE = "MUSIC_VIDEO_TYPE_OFFICIAL_SOURCE_MUSIC"
ALLOWED_VIDEO_TYPES = frozenset({VIDEO_TYPE_ATV, VIDEO_TYPE_OMV})


def is_allowed_video_type(video_type: str | None, allow_ugc: bool = False) -> bool:
    """Check if a video type should be included in results.

    Args:
        video_type: The videoType string from YouTube Music API, or None if unknown.
        allow_ugc: If True, also allow UGC and OFFICIAL_SOURCE_MUSIC types.

    Returns:
        True if the video type is allowed.
    """
    if video_type is None:
        return False
    if video_type in ALLOWED_VIDEO_TYPES:
        return True
    if allow_ugc and video_type in (VIDEO_TYPE_UGC, VIDEO_TYPE_OFFICIAL_SOURCE):
        return True
    return False


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


def get_new_releases() -> list[Album]:
    """Fetch new album releases from YouTube Music explore page.

    Uses ytmusicapi's get_explore() to retrieve the new_releases section,
    which contains recently released albums.

    Returns:
        List of Album objects representing new releases.

    Raises:
        Exception: If YouTube Music API fails (e.g., JSONDecodeError, network error)
    """
    yt = YTMusic()
    try:
        raw = yt.get_explore()
    except Exception as e:
        logger.error("YouTube Music get_explore failed: %s", e)
        raise

    albums = []
    for item in raw.get("new_releases", []):
        artists = item.get("artists", [])
        artist_name = artists[0]["name"] if artists else "Unknown Artist"

        thumbnails = item.get("thumbnails", [])
        thumbnail_url = thumbnails[-1]["url"] if thumbnails else None

        year_raw = item.get("year")
        year = int(year_raw) if year_raw and str(year_raw).isdigit() else None

        albums.append(
            Album(
                browse_id=item.get("browseId", ""),
                title=item.get("title", "Unknown"),
                artist=artist_name,
                year=year,
                track_count=None,
                thumbnail_url=thumbnail_url,
                audio_playlist_id=item.get("audioPlaylistId"),
                album_type=item.get("type"),
                is_explicit=item.get("isExplicit", False),
            )
        )

    logger.info("Found %d new release albums", len(albums))
    return albums


def get_mood_categories() -> list[MoodSection]:
    """Fetch mood & genre categories from YouTube Music.

    Returns:
        List of MoodSection objects, each containing a section title and list of categories.

    Raises:
        Exception: If YouTube Music API fails (e.g., JSONDecodeError, network error)
    """
    yt = YTMusic()
    try:
        raw = yt.get_mood_categories()
    except Exception as e:
        logger.error("YouTube Music get_mood_categories failed: %s", e)
        raise

    sections = []
    for section_title, categories in raw.items():
        items = [
            MoodCategory(title=c.get("title", "Unknown"), params=c.get("params", ""))
            for c in categories
        ]
        sections.append(MoodSection(title=section_title, categories=items))

    logger.info("Found %d mood/genre sections", len(sections))
    return sections


def get_mood_playlists(params: str) -> list[MoodPlaylist]:
    """Fetch playlists for a mood/genre category.

    Some mood/genre categories return mixed content: some sections contain
    playlist items (musicTwoRowItemRenderer) while others contain song items
    (musicResponsiveListItemRenderer). The upstream ytmusicapi library crashes
    with a KeyError when it encounters the unexpected renderer type.

    This function first attempts the standard ytmusicapi call. If it fails
    with a KeyError (the musicTwoRowItemRenderer issue), it falls back to
    manual response parsing that skips sections with incompatible renderers
    and handles individual item parse failures gracefully.

    Args:
        params: Category params string from get_mood_categories()

    Returns:
        List of MoodPlaylist objects for the given category.

    Raises:
        Exception: If YouTube Music API fails (non-KeyError exceptions propagate;
                   KeyError triggers fallback to manual parsing)
    """
    yt = YTMusic()
    try:
        raw = yt.get_mood_playlists(params)
    except KeyError as e:
        logger.warning(
            "ytmusicapi get_mood_playlists KeyError for params '%s': %s. "
            "Falling back to manual parsing.",
            params,
            e,
        )
        raw = _get_mood_playlists_fallback(yt, params)
    except Exception as e:
        logger.error("YouTube Music get_mood_playlists failed for params '%s': %s", params, e)
        raise

    playlists = []
    for item in raw:
        thumbnails = item.get("thumbnails", [])
        thumbnail_url = thumbnails[-1]["url"] if thumbnails else None
        author = _normalize_mood_playlist_author(item.get("author"))
        playlists.append(
            MoodPlaylist(
                playlist_id=item.get("playlistId", ""),
                title=item.get("title", "Unknown"),
                thumbnail_url=thumbnail_url,
                author=author,
            )
        )

    logger.info("Found %d playlists for mood/genre params", len(playlists))
    return playlists


def _normalize_mood_playlist_author(value: object) -> str | None:
    """Normalize ytmusicapi/fallback author payloads to a string."""
    if value is None:
        return None

    if isinstance(value, str):
        author = value.strip()
        return author or None

    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str):
            author = name.strip()
            return author or None
        return None

    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            if isinstance(item, str):
                author = item.strip()
                if author:
                    names.append(author)
                continue
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str):
                    author = name.strip()
                    if author:
                        names.append(author)
        if names:
            return ", ".join(names)
        return None

    author = str(value).strip()
    return author or None


def _get_mood_playlists_fallback(yt: YTMusic, params: str) -> list[dict]:
    """Manually parse mood playlists from the raw YouTube Music API response.

    This fallback handles cases where the upstream ytmusicapi get_mood_playlists
    crashes because some response sections contain musicResponsiveListItemRenderer
    items (individual songs) instead of musicTwoRowItemRenderer items (playlists).

    The function skips sections with incompatible renderers and handles individual
    item parse failures within valid sections.

    Error Handling Strategy:
        Navigation errors return empty list instead of raising. This is a fallback
        function called after the primary method fails - returning empty list is
        safer than cascading failures, allowing partial results if some sections parse.

    Args:
        yt: YTMusic instance (reused from caller to avoid re-initialization)
        params: Category params string from get_mood_categories()

    Returns:
        List of raw playlist dictionaries (same format as ytmusicapi output).
    """
    from ytmusicapi.navigation import nav, SINGLE_COLUMN_TAB, SECTION_LIST
    from ytmusicapi.parsers.browsing import (
        CAROUSEL_CONTENTS,
        GRID_ITEMS,
        parse_playlist,
    )

    MTRIR_KEY = "musicTwoRowItemRenderer"

    # WARNING: Using private ytmusicapi method _send_request()
    # This is not part of the public API and may break in future versions.
    # TODO: Monitor ytmusicapi updates for breaking changes or consider submitting
    # a PR to ytmusicapi to expose this functionality as a public method.
    # This is necessary because the public get_mood_playlists() method crashes on
    # responses containing mixed content types (playlists + songs).
    response = yt._send_request(
        "browse",
        {"browseId": "FEmusic_moods_and_genres_category", "params": params},
    )

    playlists: list[dict] = []

    try:
        sections = nav(response, SINGLE_COLUMN_TAB + SECTION_LIST)
    except Exception as e:
        logger.error("Fallback: failed to navigate mood playlists response: %s", e)
        return []

    for section_idx, section in enumerate(sections):
        # Determine content path based on section renderer type
        path: list[str] = []
        if "gridRenderer" in section:
            path = list(GRID_ITEMS)
        elif "musicCarouselShelfRenderer" in section:
            path = list(CAROUSEL_CONTENTS)
        elif "musicImmersiveCarouselShelfRenderer" in section:
            path = ["musicImmersiveCarouselShelfRenderer", "contents"]

        if not path:
            continue

        try:
            results = nav(section, path)
        except Exception:
            logger.debug("Fallback: failed to navigate section %d, skipping", section_idx)
            continue

        if not results:
            continue

        # Skip sections that don't contain playlist items (musicTwoRowItemRenderer)
        if not results or MTRIR_KEY not in results[0]:
            logger.debug(
                "Fallback: section %d uses %s, skipping (not playlist items)",
                section_idx,
                list(results[0].keys()) if results else [],
            )
            continue

        # Parse each item individually, skipping items that fail
        for item_idx, result in enumerate(results):
            if MTRIR_KEY not in result:
                continue
            try:
                parsed = parse_playlist(result[MTRIR_KEY])
                playlists.append(parsed)
            except Exception as e:
                logger.debug(
                    "Fallback: failed to parse playlist item %d in section %d: %s",
                    item_idx,
                    section_idx,
                    e,
                )

    logger.info(
        "Fallback parsing recovered %d playlists for mood/genre params '%s'",
        len(playlists),
        params,
    )
    return playlists


def get_charts(country: str = "ZZ", allow_ugc: bool = False) -> Charts:
    """Fetch chart data (top songs, artists) for a country.

    ytmusicapi get_charts returns:
      - videos: list of playlist references [{title, playlistId, thumbnails}, ...]
      - artists: flat list of artist objects [{title, browseId, rank, trend, ...}, ...]
      - genres: (country-specific) list of genre playlist references
      - countries: {selected, options}

    We fetch tracks from the first video playlist to populate chart tracks.

    Args:
        country: ISO 3166-1 Alpha-2 country code. Default 'ZZ' for global charts.

    Returns:
        Charts object with tracks and artists.

    Raises:
        Exception: If YouTube Music API fails to fetch chart metadata. Individual
                   playlist fetch errors are logged and retried with next playlist.
    """
    yt = YTMusic()
    try:
        raw = yt.get_charts(country)
    except Exception as e:
        logger.error("YouTube Music get_charts failed for country '%s': %s", country, e)
        raise

    # Videos section is a list of playlist references -- try each until one succeeds.
    # Some entries use album-style IDs (OLAK5uy_...) that fail with get_playlist,
    # so we iterate through all playlist references and use the first that works.
    # Error Handling: Individual playlist fetch errors are logged and the loop continues
    # to try the next playlist (retry pattern). This ensures we get chart data even if
    # some playlist references are broken.
    tracks = []
    video_playlists = raw.get("videos", [])
    if isinstance(video_playlists, list):
        for playlist_ref in video_playlists:
            playlist_id = playlist_ref.get("playlistId", "")
            if not playlist_id:
                continue
            try:
                playlist_data = yt.get_playlist(playlist_id, limit=100)
                for rank_idx, item in enumerate(playlist_data.get("tracks", []), 1):
                    video_id = item.get("videoId", "")
                    if not video_id:
                        continue

                    video_type = item.get("videoType")

                    # Filter by video type when available
                    if video_type is not None and not is_allowed_video_type(video_type, allow_ugc):
                        logger.debug(
                            "Skipping chart track '%s' (%s): video_type=%s",
                            item.get("title", "?"),
                            video_id,
                            video_type,
                        )
                        continue

                    artist_objects = item.get("artists", [])
                    artist_names = [a["name"] for a in artist_objects] if artist_objects else ["Unknown Artist"]
                    thumbnails = item.get("thumbnails", [])
                    album_obj = item.get("album")

                    # Extract duration
                    duration_text = item.get("duration", "0:00")
                    duration_seconds = item.get("duration_seconds") or _parse_duration(duration_text)

                    # Extract view count (ytmusicapi playlist tracks may have views)
                    view_count = item.get("views")

                    tracks.append(
                        ChartTrack(
                            video_id=video_id,
                            title=item.get("title", "Unknown"),
                            artist=artist_names[0],
                            artists=artist_names,
                            album=album_obj.get("name") if isinstance(album_obj, dict) else None,
                            thumbnail_url=thumbnails[-1]["url"] if thumbnails else None,
                            rank=str(rank_idx),
                            trend=None,
                            view_count=view_count,
                            duration_seconds=duration_seconds,
                            video_type=video_type,
                        )
                    )
                break  # Successfully fetched tracks, stop trying other playlists
            except Exception as e:
                logger.warning("Failed to fetch chart playlist '%s': %s, trying next", playlist_id, e)

    # Artists section is a flat list of artist objects (not a dict with 'items')
    artists = []
    artist_list = raw.get("artists", [])
    if isinstance(artist_list, list):
        for item in artist_list:
            thumbnails = item.get("thumbnails", [])
            artists.append(
                ChartArtist(
                    browse_id=item.get("browseId", ""),
                    title=item.get("title", "Unknown"),
                    thumbnail_url=thumbnails[-1]["url"] if thumbnails else None,
                    rank=item.get("rank"),
                    trend=item.get("trend"),
                )
            )

    logger.info("Found %d chart tracks and %d chart artists for %s", len(tracks), len(artists), country)
    return Charts(country=country, tracks=tracks, artists=artists)


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


def _parse_duration(duration_text: str) -> int:
    """Parse duration string (e.g., '3:45') to seconds."""
    try:
        parts = duration_text.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0
    except (ValueError, IndexError):
        # Invalid duration format (e.g., "NaN:30" or "--:--")
        return 0


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
    from urllib.parse import urlparse, parse_qs

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    # Check if it's a YouTube domain
    if parsed.netloc not in ['music.youtube.com', 'www.youtube.com', 'youtube.com', 'youtu.be']:
        return None

    # Handle youtu.be short URLs
    if parsed.netloc == 'youtu.be':
        video_id = parsed.path.lstrip('/')
        if video_id and len(video_id) == 11:
            return {'type': 'video', 'id': video_id}
        return None

    # Parse query parameters
    query_params = parse_qs(parsed.query)

    # Check for playlist
    if 'list' in query_params:
        playlist_id = query_params['list'][0]

        # Radio playlists (RDAM*) are not supported
        if playlist_id.startswith('RDAM'):
            # If there's also a video ID, fall back to returning just the video
            if 'v' in query_params:
                video_id = query_params['v'][0]
                if len(video_id) == 11:
                    logger.info(
                        "Radio playlist detected in URL, falling back to video: %s",
                        video_id
                    )
                    return {'type': 'video', 'id': video_id}
            # Radio-only playlist URL
            logger.warning("Radio playlist URLs are not supported: %s", playlist_id)
            return {'type': 'unsupported_radio', 'id': playlist_id}

        return {'type': 'playlist', 'id': playlist_id}

    # Check for video
    if 'v' in query_params:
        video_id = query_params['v'][0]
        if len(video_id) == 11:
            return {'type': 'video', 'id': video_id}

    return None


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
