"""Search and album routes."""

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from musicload.config import get_config
from musicload.deezer import DeezerQuotaError, is_deezer_url
from musicload.deezer import get_tracks_from_url as get_deezer_tracks_from_url
from musicload.search import (
    get_album_tracks,
    get_playlist_tracks,
    get_track_from_video_id,
    parse_youtube_url,
    search,
    search_albums,
)
from musicload.web.api_cache import TtlCache
from musicload.web.responses import sse_event as _sse_event
from musicload.web.schemas import (
    AlbumResponse,
    AlbumSearchResponse,
    AlbumTracksResponse,
    SearchResponse,
)
from musicload.web.schemas import (
    track_to_response as _track_to_response,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_search_cache = TtlCache(max_entries=100, ttl_seconds=300)
_album_search_cache = TtlCache(max_entries=100, ttl_seconds=300)
_album_tracks_cache = TtlCache(max_entries=50, ttl_seconds=900)


def _search_deezer_playlist(query: str) -> list:
    try:
        deezer_tracks = get_deezer_tracks_from_url(query)
    except DeezerQuotaError as error:
        logger.warning("Deezer quota exceeded for '%s': %s", query, error)
        raise HTTPException(status_code=503, detail=str(error))
    except Exception as error:
        logger.error("Deezer fetch failed for '%s': %s", query, error)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch Deezer playlist: {error}",
        )
    if not deezer_tracks:
        raise HTTPException(
            status_code=404,
            detail="Deezer playlist is empty or unavailable",
        )

    results = []
    for deezer_track in deezer_tracks:
        youtube_results = search(deezer_track.search_query, limit=1)
        if youtube_results:
            results.append(youtube_results[0])
        else:
            logger.warning(
                "No YouTube Music match for Deezer track: %s - %s",
                deezer_track.artist,
                deezer_track.name,
            )
    if not results:
        raise HTTPException(
            status_code=404,
            detail="No Deezer tracks could be matched on YouTube Music",
        )
    return results


def _search_youtube_url(query: str, url_info: dict, allow_ugc: bool) -> list:
    try:
        if url_info["type"] == "video":
            return [get_track_from_video_id(url_info["id"])]
        if url_info["type"] == "playlist":
            results = get_playlist_tracks(url_info["id"], allow_ugc=allow_ugc)
            if not results:
                raise HTTPException(
                    status_code=404, detail="Playlist is empty or unavailable"
                )
            return results
        if url_info["type"] == "unsupported_radio":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Radio playlists are not supported. Please use a regular "
                    "playlist or single track URL."
                ),
            )
        raise HTTPException(status_code=400, detail="Unsupported URL type")
    except ValueError as error:
        logger.warning("URL fetch failed for '%s': %s", query, error)
        raise HTTPException(
            status_code=404,
            detail=f"Video or playlist not found: {error}",
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.error("URL fetch failed for '%s': %s", query, error)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch from URL: {error}",
        )


def _cached_text_search(query: str) -> list:
    cache_key = f"search:{query}"
    cached = _search_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        results = search(query, limit=20)
    except Exception as error:
        logger.error("Search failed for query '%s': %s", query, error)
        raise HTTPException(status_code=500, detail=f"Search failed: {error}")
    _search_cache.put(cache_key, results)
    return results


async def _match_deezer_track(deezer_track):
    try:
        youtube_results = await asyncio.to_thread(
            search, deezer_track.search_query, 1
        )
    except Exception as error:
        logger.warning(
            "Search failed for Deezer track '%s - %s': %s",
            deezer_track.artist,
            deezer_track.name,
            error,
        )
        return None
    return youtube_results[0] if youtube_results else None


async def _deezer_playlist_events(
    request: Request, query: str
) -> AsyncIterator[str]:
    yield _sse_event(
        "progress",
        {"stage": "fetching", "message": "Fetching Deezer playlist tracks..."},
    )
    try:
        deezer_tracks = await asyncio.to_thread(get_deezer_tracks_from_url, query)
    except DeezerQuotaError as error:
        logger.warning("Deezer quota exceeded for '%s': %s", query, error)
        yield _sse_event("failure", {"message": str(error)})
        return
    except Exception as error:
        logger.error("Deezer fetch failed for '%s': %s", query, error)
        yield _sse_event(
            "failure", {"message": f"Failed to fetch Deezer playlist: {error}"}
        )
        return
    if not deezer_tracks:
        yield _sse_event(
            "failure", {"message": "Deezer playlist is empty or unavailable"}
        )
        return

    total = len(deezer_tracks)
    matched_tracks = []
    yield _sse_event(
        "progress",
        {"stage": "matching", "total": total, "processed": 0, "matched": 0},
    )
    for processed, deezer_track in enumerate(deezer_tracks, 1):
        if await request.is_disconnected():
            return
        matched_track = await _match_deezer_track(deezer_track)
        if matched_track:
            matched_tracks.append(matched_track)
        yield _sse_event(
            "progress",
            {
                "stage": "matching",
                "total": total,
                "processed": processed,
                "matched": len(matched_tracks),
            },
        )
    if not matched_tracks:
        yield _sse_event(
            "failure",
            {"message": "No Deezer tracks could be matched on YouTube Music"},
        )
        return
    payload = [_track_to_response(track).model_dump() for track in matched_tracks]
    yield _sse_event("complete", {"results": payload, "total": len(payload)})


def _playlist_url_failure(url_info: dict | None) -> str | None:
    if not url_info or url_info["type"] != "playlist":
        if url_info and url_info["type"] == "unsupported_radio":
            return (
                "Radio playlists are not supported. Please use a regular playlist "
                "or single track URL."
            )
        return "Only playlist URLs are supported for streaming search"
    return None


async def _youtube_playlist_events(query: str) -> AsyncIterator[str]:
    url_info = parse_youtube_url(query)
    failure = _playlist_url_failure(url_info)
    if failure:
        yield _sse_event("failure", {"message": failure})
        return

    yield _sse_event(
        "progress", {"stage": "fetching", "message": "Fetching playlist tracks..."}
    )
    try:
        config = get_config()
        tracks = await asyncio.to_thread(
            get_playlist_tracks, url_info["id"], config.allow_ugc
        )
    except ValueError as error:
        yield _sse_event("failure", {"message": str(error)})
        return
    except Exception as error:
        logger.error("Playlist fetch failed for '%s': %s", query, error)
        yield _sse_event(
            "failure", {"message": f"Failed to fetch playlist: {error}"}
        )
        return
    if not tracks:
        yield _sse_event("failure", {"message": "Playlist is empty or unavailable"})
        return

    payload = [_track_to_response(track).model_dump() for track in tracks]
    yield _sse_event(
        "progress",
        {
            "stage": "resolved",
            "total": len(payload),
            "processed": len(payload),
            "matched": len(payload),
            "message": f"Found {len(payload)} tracks",
        },
    )
    yield _sse_event("complete", {"results": payload, "total": len(payload)})


async def _playlist_events(request: Request, query: str) -> AsyncIterator[str]:
    try:
        events = (
            _deezer_playlist_events(request, query)
            if is_deezer_url(query)
            else _youtube_playlist_events(query)
        )
        async for event in events:
            yield event
    except Exception as error:
        logger.error("Playlist streaming search failed for '%s': %s", query, error)
        yield _sse_event(
            "failure", {"message": f"Playlist search failed: {error}"}
        )


@router.get("/api/search", response_model=SearchResponse)
async def api_search(
    q: str = Query(
        ...,
        min_length=1,
        description=(
            "Search query or supported URL (YouTube Music, YouTube, Deezer playlist)"
        ),
    ),
):
    """Search for music on YouTube Music or fetch tracks from a supported URL."""
    config = get_config()
    if is_deezer_url(q):
        results = _search_deezer_playlist(q)
    else:
        url_info = parse_youtube_url(q)
        if url_info:
            results = _search_youtube_url(q, url_info, config.allow_ugc)
        else:
            results = _cached_text_search(q)

    return SearchResponse(
        query=q,
        results=[_track_to_response(track) for track in results],
    )


@router.get("/api/search/playlist/stream")
async def api_search_playlist_stream(
    request: Request,
    q: str = Query(
        ...,
        min_length=1,
        description="Playlist URL (YouTube Music, YouTube, Deezer)",
    ),
):
    """Stream playlist search progress and results via SSE."""
    return StreamingResponse(
        _playlist_events(request, q),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/search/albums", response_model=AlbumSearchResponse)
async def api_search_albums(q: str = Query(..., min_length=1)):
    """Search YouTube Music for albums."""
    cache_key = f"album_search:{q}"
    cached = _album_search_cache.get(cache_key)
    if cached is not None:
        results = cached
    else:
        try:
            results = search_albums(q, limit=20)
        except Exception as error:
            logger.error("Album search failed for query '%s': %s", q, error)
            raise HTTPException(
                status_code=500,
                detail=f"Album search failed: {error}",
            )
        _album_search_cache.put(cache_key, results)

    return AlbumSearchResponse(
        query=q,
        results=[AlbumResponse(**album.__dict__) for album in results],
    )


@router.get("/api/album/{browse_id}/tracks", response_model=AlbumTracksResponse)
async def api_get_album_tracks(browse_id: str):
    """Get all tracks for an album."""
    cache_key = f"album_tracks:{browse_id}"
    cached = _album_tracks_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        tracks = get_album_tracks(browse_id)
        if not tracks:
            raise HTTPException(
                status_code=404, detail="No tracks found for this album"
            )

        response = AlbumTracksResponse(
            browse_id=browse_id,
            album_title=tracks[0].album if tracks else "Unknown Album",
            tracks=[_track_to_response(track) for track in tracks],
        )
        _album_tracks_cache.put(cache_key, response)
        return response
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Failed to get album tracks: %s", error)
        raise HTTPException(status_code=500, detail=str(error))
