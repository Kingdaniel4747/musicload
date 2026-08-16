"""Mood, chart, release, and playlist exploration routes."""

import logging
import re

from fastapi import APIRouter, HTTPException, Query

from musicload.config import get_config
from musicload.explore import (
    get_charts,
    get_mood_categories,
    get_mood_playlists,
    get_new_releases,
)
from musicload.search import get_playlist_tracks
from musicload.web.api_cache import TtlCache
from musicload.web.schemas import (
    AlbumResponse,
    ChartArtistResponse,
    ChartsResponse,
    ChartTrackResponse,
    MoodCategoryResponse,
    MoodPlaylistResponse,
    MoodSectionResponse,
)
from musicload.web.schemas import (
    track_to_response as _track_to_response,
)

router = APIRouter(prefix="/api/explore")
logger = logging.getLogger(__name__)

_moods_cache = TtlCache(max_entries=1, ttl_seconds=3600)
_mood_playlists_cache = TtlCache(max_entries=25, ttl_seconds=1800)
_charts_cache = TtlCache(max_entries=10, ttl_seconds=1800)
_playlist_tracks_cache = TtlCache(max_entries=25, ttl_seconds=900)
_new_releases_cache = TtlCache(max_entries=1, ttl_seconds=1800)


@router.get("/moods")
async def api_explore_moods():
    """Get mood & genre categories."""
    cached = _moods_cache.get("moods")
    if cached is not None:
        return cached

    try:
        sections = get_mood_categories()
        result = [
            MoodSectionResponse(
                title=section.title,
                categories=[
                    MoodCategoryResponse(title=category.title, params=category.params)
                    for category in section.categories
                ],
            )
            for section in sections
        ]
        _moods_cache.put("moods", result)
        return result
    except Exception as error:
        logger.error("Failed to get mood categories: %s", error)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get mood categories: {error}",
        )


@router.get("/mood-playlists")
async def api_explore_mood_playlists(
    params: str = Query(..., description="Category params from moods endpoint"),
):
    """Get playlists for a mood/genre category."""
    cache_key = f"mood_playlists:{params}"
    cached = _mood_playlists_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        playlists = get_mood_playlists(params)
        result = [
            MoodPlaylistResponse(
                playlist_id=playlist.playlist_id,
                title=playlist.title,
                thumbnail_url=playlist.thumbnail_url,
                author=playlist.author,
            )
            for playlist in playlists
        ]
        _mood_playlists_cache.put(cache_key, result)
        return result
    except Exception as error:
        logger.error("Failed to get mood playlists: %s", error)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get mood playlists: {error}",
        )


@router.get("/charts")
async def api_explore_charts(
    country: str = Query(
        "ZZ", description="ISO 3166-1 Alpha-2 country code"
    ),
):
    """Get current music charts."""
    if not re.match(r"^[A-Z]{2}$", country):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid country code '{country}': must be a 2-letter uppercase "
                "ISO 3166-1 Alpha-2 code (e.g., 'US', 'GB', 'ZZ')"
            ),
        )

    cache_key = f"charts:{country}"
    cached = _charts_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        config = get_config()
        charts = get_charts(country, allow_ugc=config.allow_ugc)
        response = ChartsResponse(
            country=charts.country,
            tracks=[
                ChartTrackResponse(
                    video_id=track.video_id,
                    title=track.title,
                    artist=track.artist,
                    artists=track.artists,
                    album=track.album,
                    thumbnail_url=track.thumbnail_url,
                    rank=track.rank,
                    trend=track.trend,
                    view_count=track.view_count,
                    duration=track.duration_display,
                    video_type=track.video_type,
                )
                for track in charts.tracks
            ],
            artists=[
                ChartArtistResponse(
                    browse_id=artist.browse_id,
                    title=artist.title,
                    thumbnail_url=artist.thumbnail_url,
                    rank=artist.rank,
                    trend=artist.trend,
                )
                for artist in charts.artists
            ],
        )
        _charts_cache.put(cache_key, response)
        return response
    except Exception as error:
        logger.error("Failed to get charts: %s", error)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get charts: {error}",
        )


@router.get("/new-releases")
async def api_explore_new_releases():
    """Get new album releases from YouTube Music."""
    cached = _new_releases_cache.get("new_releases")
    if cached is not None:
        return cached

    try:
        albums = get_new_releases()
        result = [AlbumResponse(**album.model_dump()) for album in albums]
        _new_releases_cache.put("new_releases", result)
        return result
    except Exception as error:
        logger.error("Failed to get new releases: %s", error)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get new releases: {error}",
        )


@router.get("/playlist/{playlist_id}/tracks")
async def api_explore_playlist_tracks(playlist_id: str):
    """Get tracks from a YouTube Music playlist (mood/genre playlist)."""
    cache_key = f"playlist_tracks:{playlist_id}"
    cached = _playlist_tracks_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        config = get_config()
        tracks = get_playlist_tracks(playlist_id, allow_ugc=config.allow_ugc)
        result = {
            "playlist_id": playlist_id,
            "tracks": [_track_to_response(track) for track in tracks],
        }
        _playlist_tracks_cache.put(cache_key, result)
        return result
    except ValueError as error:
        logger.warning("Playlist unavailable: %s", error)
        raise HTTPException(status_code=404, detail=str(error))
    except Exception as error:
        logger.error("Failed to get playlist tracks: %s", error)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get playlist tracks: {error}",
        )
