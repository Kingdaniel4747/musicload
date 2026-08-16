"""Browse moods, playlists, releases, and charts on YouTube Music."""

import logging

from ytmusicapi import YTMusic
from ytmusicapi.navigation import SECTION_LIST, SINGLE_COLUMN_TAB, nav
from ytmusicapi.parsers.browsing import CAROUSEL_CONTENTS, GRID_ITEMS, parse_playlist

from musicload.models.search import (
    Album,
    ChartArtist,
    Charts,
    ChartTrack,
    MoodCategory,
    MoodPlaylist,
    MoodSection,
)
from musicload.search_utils import (
    is_allowed_video_type,
)
from musicload.search_utils import (
    parse_duration as _parse_duration,
)

logger = logging.getLogger(__name__)

_PLAYLIST_RENDERER = "musicTwoRowItemRenderer"
_SECTION_PATHS = (
    ("gridRenderer", list(GRID_ITEMS)),
    ("musicCarouselShelfRenderer", list(CAROUSEL_CONTENTS)),
    (
        "musicImmersiveCarouselShelfRenderer",
        ["musicImmersiveCarouselShelfRenderer", "contents"],
    ),
)


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
        logger.error(
            "YouTube Music get_mood_playlists failed for params '%s': %s", params, e
        )
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
    if isinstance(value, list):
        names = [name for item in value if (name := _author_item_name(item))]
        return ", ".join(names) or None
    if isinstance(value, (str, dict)):
        return _author_item_name(value)
    return str(value).strip() or None


def _author_item_name(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("name")
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _section_path(section: dict) -> list[str] | None:
    return next((path for key, path in _SECTION_PATHS if key in section), None)


def _playlist_section_results(section: dict, section_index: int) -> list[dict]:
    path = _section_path(section)
    if not path:
        return []
    try:
        results = nav(section, path)
    except Exception:
        logger.debug("Fallback: failed to navigate section %d, skipping", section_index)
        return []
    if not results:
        return []
    if _PLAYLIST_RENDERER not in results[0]:
        logger.debug(
            "Fallback: section %d uses %s, skipping (not playlist items)",
            section_index,
            list(results[0].keys()),
        )
        return []
    return results


def _parse_playlist_section(results: list[dict], section_index: int) -> list[dict]:
    playlists = []
    for item_index, result in enumerate(results):
        if _PLAYLIST_RENDERER not in result:
            continue
        try:
            playlists.append(parse_playlist(result[_PLAYLIST_RENDERER]))
        except Exception as error:
            logger.debug(
                "Fallback: failed to parse playlist item %d in section %d: %s",
                item_index,
                section_index,
                error,
            )
    return playlists


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

    for section_index, section in enumerate(sections):
        results = _playlist_section_results(section, section_index)
        playlists.extend(_parse_playlist_section(results, section_index))

    logger.info(
        "Fallback parsing recovered %d playlists for mood/genre params '%s'",
        len(playlists),
        params,
    )
    return playlists


def _chart_track_from_item(item: dict, rank: int, allow_ugc: bool) -> ChartTrack | None:
    video_id = item.get("videoId", "")
    if not video_id:
        return None
    video_type = item.get("videoType")
    if video_type is not None and not is_allowed_video_type(video_type, allow_ugc):
        logger.debug(
            "Skipping chart track '%s' (%s): video_type=%s",
            item.get("title", "?"),
            video_id,
            video_type,
        )
        return None

    artist_objects = item.get("artists", [])
    artist_names = (
        [artist["name"] for artist in artist_objects]
        if artist_objects
        else ["Unknown Artist"]
    )
    thumbnails = item.get("thumbnails", [])
    album = item.get("album")
    duration = item.get("duration", "0:00")
    return ChartTrack(
        video_id=video_id,
        title=item.get("title", "Unknown"),
        artist=artist_names[0],
        artists=artist_names,
        album=album.get("name") if isinstance(album, dict) else None,
        thumbnail_url=thumbnails[-1]["url"] if thumbnails else None,
        rank=str(rank),
        trend=None,
        view_count=item.get("views"),
        duration_seconds=item.get("duration_seconds") or _parse_duration(duration),
        video_type=video_type,
    )


def _get_chart_tracks(yt: YTMusic, raw: dict, allow_ugc: bool) -> list[ChartTrack]:
    playlist_references = raw.get("videos", [])
    if not isinstance(playlist_references, list):
        return []

    for playlist_reference in playlist_references:
        playlist_id = playlist_reference.get("playlistId", "")
        if not playlist_id:
            continue
        try:
            playlist = yt.get_playlist(playlist_id, limit=100)
        except Exception as error:
            logger.warning(
                "Failed to fetch chart playlist '%s': %s, trying next",
                playlist_id,
                error,
            )
            continue
        return [
            track
            for rank, item in enumerate(playlist.get("tracks", []), 1)
            if (track := _chart_track_from_item(item, rank, allow_ugc)) is not None
        ]
    return []


def _get_chart_artists(raw: dict) -> list[ChartArtist]:
    artist_items = raw.get("artists", [])
    if not isinstance(artist_items, list):
        return []
    artists = []
    for item in artist_items:
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
    return artists


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

    tracks = _get_chart_tracks(yt, raw, allow_ugc)
    artists = _get_chart_artists(raw)

    logger.info(
        "Found %d chart tracks and %d chart artists for %s",
        len(tracks),
        len(artists),
        country,
    )
    return Charts(country=country, tracks=tracks, artists=artists)
