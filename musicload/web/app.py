"""FastAPI web application for Musicload."""

import asyncio
import html
import json
import logging
import re
import urllib.parse
from pathlib import Path

import yt_dlp
import httpx
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, RedirectResponse

from musicload import __version__
from musicload.config import get_config
from musicload.download import download
from musicload.playlist import read_m3u, remove_from_m3u
from musicload.queue import QueueManager
from musicload.search import search
from musicload.web.api_cache import TtlCache
from musicload.web.image_proxy import ImageProxyService, validate_image_url
from musicload.yt_dlp_wrapper import extract_info_with_retry

app = FastAPI(title="Musicload", description="Search and download music from YouTube Music")
logger = logging.getLogger(__name__)

# Configure CORS
config = get_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global queue manager
queue_manager: QueueManager | None = None

# Global image proxy service
_image_proxy: ImageProxyService | None = None

# API response caches (TTL in seconds)
_search_cache = TtlCache(max_entries=200, ttl_seconds=300)        # 5 min
_album_search_cache = TtlCache(max_entries=200, ttl_seconds=300)  # 5 min
_album_tracks_cache = TtlCache(max_entries=100, ttl_seconds=900)  # 15 min
_moods_cache = TtlCache(max_entries=1, ttl_seconds=3600)          # 1 hour
_mood_playlists_cache = TtlCache(max_entries=50, ttl_seconds=1800)  # 30 min
_charts_cache = TtlCache(max_entries=20, ttl_seconds=1800)        # 30 min
_playlist_tracks_cache = TtlCache(max_entries=50, ttl_seconds=900)  # 15 min
_new_releases_cache = TtlCache(max_entries=1, ttl_seconds=1800)    # 30 min

# Setup templates and static files
templates_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(templates_dir))
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

_AUTH_PUBLIC_PATHS = {
    "/login",
    "/api/auth/login",
    "/api/auth/status",
    "/sw.js",
}


@app.middleware("http")
async def require_navidrome_login(request: Request, call_next):
    """Protect pages and APIs when Navidrome authentication is configured."""
    if (
        not config.navidrome_url
        or request.url.path in _AUTH_PUBLIC_PATHS
        or request.url.path.startswith("/static/")
    ):
        return await call_next(request)
    if request.session.get("username"):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    next_path = urllib.parse.quote(request.url.path, safe="/")
    return RedirectResponse(f"/login?next={next_path}", status_code=303)


if config.navidrome_url:
    if not config.session_secret or len(config.session_secret) < 32:
        raise RuntimeError(
            "NAVIDROME_URL requires MUSICLOAD_SESSION_SECRET with at least 32 characters"
        )
    from musicload.web.auth import SignedSessionMiddleware

    app.add_middleware(
        SignedSessionMiddleware,
        secret_key=config.session_secret,
        session_cookie="musicload_session",
        max_age=60 * 60 * 24 * 7,
        https_only=config.session_https_only,
    )


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    """Serve the worker at the origin root so it can control the installed PWA."""
    return FileResponse(
        static_dir / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )

_SAFE_USERNAME_RE = re.compile(r"[^a-zA-Z0-9._-]")
_SHARE_GOOGLE_HOSTS = {"share.google"}
_SHARE_REDIRECT_HOSTS = {
    "share.google",
    "google.com",
    "www.google.com",
    "music.youtube.com",
    "youtube.com",
    "www.youtube.com",
}


def _extract_google_share_query(url: str, page_html: str = "") -> str | None:
    """Extract a readable song query from a Google short-link destination."""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    for key in ("q", "query", "text"):
        value = params.get(key, [""])[0].strip()
        if value:
            return value[:200]

    title_match = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.IGNORECASE | re.DOTALL)
    if not title_match:
        return None
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1))).strip()
    title = re.sub(r"\s*[-|]\s*Google Search\s*$", "", title, flags=re.IGNORECASE)
    return title[:200] if title and title.lower() != "google search" else None


async def _resolve_google_share_link(shared_url: str) -> str | None:
    """Follow only Google/YouTube redirects and return a safe text query."""
    current_url = shared_url
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Musicload/1.0)"}
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False, headers=headers) as client:
        for _ in range(5):
            parsed = urllib.parse.urlparse(current_url)
            if parsed.scheme != "https" or parsed.hostname not in _SHARE_REDIRECT_HOSTS:
                raise ValueError("Unsupported share link")

            response = await client.get(current_url)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return None
                current_url = urllib.parse.urljoin(current_url, location)
                continue

            return _extract_google_share_query(str(response.url), response.text)
    return None


def _get_remote_user(http_request: Request, config) -> str | None:
    """Extract and sanitize Remote-User header. Returns None if multi-user disabled or header absent."""
    if not config.multi_user:
        return None
    raw = http_request.headers.get("Remote-User")
    if not raw:
        return None
    sanitized = _SAFE_USERNAME_RE.sub("_", raw.strip())[:64]
    return sanitized if sanitized else None


@app.on_event("startup")
async def startup_event():
    """Initialize queue manager and image proxy on startup."""
    global queue_manager, _image_proxy
    queue_manager = QueueManager()
    await queue_manager.start()
    _image_proxy = ImageProxyService()


@app.on_event("shutdown")
async def shutdown_event():
    """Stop queue manager and image proxy on shutdown."""
    if queue_manager:
        await queue_manager.stop()
    if _image_proxy:
        await _image_proxy.close()


class DownloadRequest(BaseModel):
    """Request body for download endpoint."""

    video_id: str
    title: str
    artist: str
    artists: list[str] | None = None
    audio_format: str = "opus"


class LoginRequest(BaseModel):
    """Navidrome credentials used only for the current login attempt."""

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class DownloadResponse(BaseModel):
    """Response body for download endpoint."""

    success: bool
    message: str
    file_path: str | None = None
    file_name: str | None = None


class TrackResponse(BaseModel):
    """Track data for API responses."""

    video_id: str
    title: str
    artist: str
    artists: list[str]
    album: str | None
    duration: str
    thumbnail_url: str | None
    view_count: str | None
    video_type: str | None = None


def _track_to_response(track) -> TrackResponse:
    """Convert a Track-like object into a TrackResponse."""
    return TrackResponse(
        video_id=track.video_id,
        title=track.title,
        artist=track.artist,
        artists=track.artists,
        album=track.album,
        duration=track.duration_display,
        thumbnail_url=track.thumbnail_url,
        view_count=track.view_count,
        video_type=getattr(track, "video_type", None),
    )


def _sse_event(event: str, payload: dict) -> str:
    """Format a Server-Sent Event message."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


class SearchResponse(BaseModel):
    """Response body for search endpoint."""

    query: str
    results: list[TrackResponse]


class AlbumResponse(BaseModel):
    """Album data for API responses."""

    browse_id: str
    title: str
    artist: str
    year: int | None
    track_count: int | None
    thumbnail_url: str | None
    audio_playlist_id: str | None = None
    album_type: str | None = None
    is_explicit: bool = False


class AlbumSearchResponse(BaseModel):
    """Response body for album search endpoint."""

    query: str
    results: list[AlbumResponse]


class AlbumTracksResponse(BaseModel):
    """Response body for album tracks endpoint."""

    browse_id: str
    album_title: str
    tracks: list[TrackResponse]


class StreamUrlResponse(BaseModel):
    """Response for stream URL endpoint."""

    video_id: str
    url: str
    expires_in: int
    is_hls: bool = False


class MoodCategoryResponse(BaseModel):
    """A mood/genre category."""
    title: str
    params: str

class MoodSectionResponse(BaseModel):
    """A section of mood/genre categories."""
    title: str
    categories: list[MoodCategoryResponse]

class MoodPlaylistResponse(BaseModel):
    """A playlist from a mood/genre category."""
    playlist_id: str
    title: str
    thumbnail_url: str | None
    author: str | None

class ChartTrackResponse(BaseModel):
    """A chart track."""
    video_id: str
    title: str
    artist: str
    artists: list[str]
    album: str | None
    thumbnail_url: str | None
    rank: str | None
    trend: str | None
    view_count: str | None = None
    duration: str | None = None
    video_type: str | None = None

class ChartArtistResponse(BaseModel):
    """A chart artist."""
    browse_id: str
    title: str
    thumbnail_url: str | None
    rank: str | None
    trend: str | None

class ChartsResponse(BaseModel):
    """Charts response."""
    country: str
    tracks: list[ChartTrackResponse]
    artists: list[ChartArtistResponse]


class LibraryTrackResponse(BaseModel):
    """A local audio file on disk."""
    entry_path: str
    title: str
    artist: str
    album: str | None
    duration: str | None
    file_size: int
    modified_at: float


class LibraryTracksResponse(BaseModel):
    """Response body for the local library endpoint."""
    tracks: list[LibraryTrackResponse]
    total: int
    limit: int
    offset: int


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main search page."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "version": __version__,
            "auth_enabled": bool(config.navidrome_url),
            "auth_user": request.session.get("username") if config.navidrome_url else None,
        },
    )


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request):
    """Render the Navidrome login page."""
    if not config.navidrome_url:
        return RedirectResponse("/", status_code=303)
    if request.session.get("username"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html")


@app.post("/api/auth/login")
async def api_login(login: LoginRequest, request: Request):
    """Authenticate against Navidrome and create a signed local session."""
    if not config.navidrome_url:
        raise HTTPException(status_code=404, detail="Navidrome login is not configured")

    from musicload.web.auth import (
        AuthenticationError,
        authenticate_navidrome,
        check_login_rate_limit,
        clear_login_attempts,
    )

    client = request.client.host if request.client else "unknown"
    try:
        check_login_rate_limit(client)
        username = login.username.strip()
        if not username or not login.password:
            raise AuthenticationError("Invalid username or password.")
        user = await authenticate_navidrome(config.navidrome_url, username, login.password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    clear_login_attempts(client)
    request.session.clear()
    request.session.update({"username": user.username, "is_admin": user.is_admin})
    return {"success": True, "username": user.username, "is_admin": user.is_admin}


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    """Destroy the current Musicload session."""
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/api/auth/status")
async def api_auth_status(request: Request):
    """Return authentication state without exposing credentials."""
    if not config.navidrome_url:
        return {"enabled": False, "authenticated": True}
    return {
        "enabled": True,
        "authenticated": bool(request.session.get("username")),
        "username": request.session.get("username"),
        "is_admin": bool(request.session.get("is_admin")),
    }


@app.get("/api/share/resolve")
async def resolve_google_share(url: str = Query(..., min_length=1)):
    """Resolve a share.google short link to a song query for the PWA share flow."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _SHARE_GOOGLE_HOSTS:
        raise HTTPException(status_code=400, detail="Only share.google links are supported")
    try:
        return {"query": await _resolve_google_share_link(url)}
    except httpx.HTTPError as exc:
        logger.warning("Could not resolve Google share link: %s", exc)
        return {"query": None}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/search", response_model=SearchResponse)
async def api_search(q: str = Query(..., min_length=1, description="Search query or supported URL (YouTube Music, YouTube, Deezer playlist)")):
    """Search for music on YouTube Music or fetch tracks from a supported URL."""
    import logging
    logger = logging.getLogger(__name__)
    config = get_config()

    from musicload.deezer import DeezerQuotaError
    from musicload.deezer import get_tracks_from_url as get_deezer_tracks_from_url
    from musicload.deezer import is_deezer_url
    # Import URL handling functions from search module
    from musicload.search import parse_youtube_url, get_track_from_video_id, get_playlist_tracks

    if is_deezer_url(q):
        # Deezer playlist URL -> resolve each Deezer track to YouTube Music
        try:
            deezer_tracks = get_deezer_tracks_from_url(q)
        except DeezerQuotaError as e:
            logger.warning("Deezer quota exceeded for '%s': %s", q, e)
            raise HTTPException(
                status_code=503,
                detail=str(e),
            )
        except Exception as e:
            logger.error("Deezer fetch failed for '%s': %s", q, e)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch Deezer playlist: {str(e)}",
            )

        if not deezer_tracks:
            raise HTTPException(
                status_code=404,
                detail="Deezer playlist is empty or unavailable",
            )

        results = []
        for dz_track in deezer_tracks:
            yt_results = search(dz_track.search_query, limit=1)
            if yt_results:
                results.append(yt_results[0])
            else:
                logger.warning(
                    "No YouTube Music match for Deezer track: %s - %s",
                    dz_track.artist,
                    dz_track.name,
                )

        if not results:
            raise HTTPException(
                status_code=404,
                detail="No Deezer tracks could be matched on YouTube Music",
            )
    else:
        # Check if query is a YouTube URL
        url_info = parse_youtube_url(q)

        if url_info:
            # Handle URL input
            try:
                if url_info['type'] == 'video':
                    # Single track from video_id
                    track = get_track_from_video_id(url_info['id'])
                    results = [track]
                elif url_info['type'] == 'playlist':
                    # All tracks from playlist (no limit)
                    # Pass allow_ugc=True so web UI can show all tracks with UGC badge
                    results = get_playlist_tracks(url_info['id'], allow_ugc=config.allow_ugc)
                    if not results:
                        raise HTTPException(
                            status_code=404,
                            detail="Playlist is empty or unavailable"
                        )
                elif url_info['type'] == 'unsupported_radio':
                    raise HTTPException(
                        status_code=400,
                        detail="Radio playlists are not supported. Please use a regular playlist or single track URL."
                    )
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="Unsupported URL type"
                    )
            except ValueError as e:
                # Video/playlist not found
                logger.warning("URL fetch failed for '%s': %s", q, e)
                raise HTTPException(
                    status_code=404,
                    detail=f"Video or playlist not found: {str(e)}"
                )
            except HTTPException:
                # Re-raise HTTPException as-is
                raise
            except Exception as e:
                logger.error("URL fetch failed for '%s': %s", q, e)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to fetch from URL: {str(e)}"
                )
        else:
            # Handle regular text search (existing logic) — cached
            cached = _search_cache.get(f"search:{q}")
            if cached is not None:
                results = cached
            else:
                try:
                    results = search(q, limit=20)
                except Exception as e:
                    logger.error("Search failed for query '%s': %s", q, e)
                    raise HTTPException(
                        status_code=500,
                        detail=f"Search failed: {str(e)}"
                    )
                _search_cache.put(f"search:{q}", results)

    return SearchResponse(
        query=q,
        results=[_track_to_response(track) for track in results],
    )


@app.get("/api/search/playlist/stream")
async def api_search_playlist_stream(
    request: Request,
    q: str = Query(..., min_length=1, description="Playlist URL (YouTube Music, YouTube, Deezer)"),
):
    """Stream playlist search progress and results via SSE."""
    import logging

    logger = logging.getLogger(__name__)

    from musicload.deezer import DeezerQuotaError
    from musicload.deezer import get_tracks_from_url as get_deezer_tracks_from_url
    from musicload.deezer import is_deezer_url
    from musicload.search import get_playlist_tracks, parse_youtube_url, search as yt_search

    async def event_generator():
        try:
            if is_deezer_url(q):
                yield _sse_event(
                    "progress",
                    {
                        "stage": "fetching",
                        "message": "Fetching Deezer playlist tracks...",
                    },
                )

                try:
                    deezer_tracks = await asyncio.to_thread(get_deezer_tracks_from_url, q)
                except DeezerQuotaError as e:
                    logger.warning("Deezer quota exceeded for '%s': %s", q, e)
                    yield _sse_event("failure", {"message": str(e)})
                    return
                except Exception as e:
                    logger.error("Deezer fetch failed for '%s': %s", q, e)
                    yield _sse_event(
                        "failure",
                        {"message": f"Failed to fetch Deezer playlist: {str(e)}"},
                    )
                    return

                if not deezer_tracks:
                    yield _sse_event(
                        "failure",
                        {"message": "Deezer playlist is empty or unavailable"},
                    )
                    return

                total = len(deezer_tracks)
                processed = 0
                matched = 0
                results = []

                yield _sse_event(
                    "progress",
                    {
                        "stage": "matching",
                        "total": total,
                        "processed": processed,
                        "matched": matched,
                    },
                )

                for dz_track in deezer_tracks:
                    if await request.is_disconnected():
                        return

                    processed += 1
                    try:
                        yt_results = await asyncio.to_thread(
                            yt_search, dz_track.search_query, 1
                        )
                    except Exception as e:
                        logger.warning(
                            "Search failed for Deezer track '%s - %s': %s",
                            dz_track.artist,
                            dz_track.name,
                            e,
                        )
                        yt_results = []

                    if yt_results:
                        results.append(yt_results[0])
                        matched += 1

                    yield _sse_event(
                        "progress",
                        {
                            "stage": "matching",
                            "total": total,
                            "processed": processed,
                            "matched": matched,
                        },
                    )

                if not results:
                    yield _sse_event(
                        "failure",
                        {"message": "No Deezer tracks could be matched on YouTube Music"},
                    )
                    return

                payload_results = [_track_to_response(track).model_dump() for track in results]
                yield _sse_event(
                    "complete",
                    {
                        "results": payload_results,
                        "total": len(payload_results),
                    },
                )
                return

            url_info = parse_youtube_url(q)
            if not url_info:
                yield _sse_event(
                    "failure",
                    {"message": "Only playlist URLs are supported for streaming search"},
                )
                return

            if url_info["type"] == "unsupported_radio":
                yield _sse_event(
                    "failure",
                    {
                        "message": "Radio playlists are not supported. Please use a regular playlist or single track URL.",
                    },
                )
                return

            if url_info["type"] != "playlist":
                yield _sse_event(
                    "failure",
                    {"message": "Only playlist URLs are supported for streaming search"},
                )
                return

            yield _sse_event(
                "progress",
                {
                    "stage": "fetching",
                    "message": "Fetching playlist tracks...",
                },
            )

            try:
                config = get_config()
                tracks = await asyncio.to_thread(get_playlist_tracks, url_info["id"], config.allow_ugc)
            except ValueError as e:
                yield _sse_event("failure", {"message": str(e)})
                return
            except Exception as e:
                logger.error("Playlist fetch failed for '%s': %s", q, e)
                yield _sse_event(
                    "failure", {"message": f"Failed to fetch playlist: {str(e)}"}
                )
                return

            if not tracks:
                yield _sse_event(
                    "failure", {"message": "Playlist is empty or unavailable"}
                )
                return

            payload_results = [_track_to_response(track).model_dump() for track in tracks]
            yield _sse_event(
                "progress",
                {
                    "stage": "resolved",
                    "total": len(payload_results),
                    "processed": len(payload_results),
                    "matched": len(payload_results),
                    "message": f"Found {len(payload_results)} tracks",
                },
            )
            yield _sse_event(
                "complete",
                {
                    "results": payload_results,
                    "total": len(payload_results),
                },
            )
        except Exception as e:
            logger.error("Playlist streaming search failed for '%s': %s", q, e)
            yield _sse_event(
                "failure", {"message": f"Playlist search failed: {str(e)}"}
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/search/albums", response_model=AlbumSearchResponse)
async def api_search_albums(q: str = Query(..., min_length=1)):
    """Search YouTube Music for albums."""
    from musicload.search import search_albums
    import logging

    logger = logging.getLogger(__name__)

    cached = _album_search_cache.get(f"album_search:{q}")
    if cached is not None:
        results = cached
    else:
        try:
            results = search_albums(q, limit=20)
        except Exception as e:
            logger.error("Album search failed for query '%s': %s", q, e)
            raise HTTPException(
                status_code=500,
                detail=f"Album search failed: {str(e)}"
            )
        _album_search_cache.put(f"album_search:{q}", results)

    return AlbumSearchResponse(
        query=q,
        results=[AlbumResponse(**album.__dict__) for album in results],
    )


@app.get("/api/album/{browse_id}/tracks", response_model=AlbumTracksResponse)
async def api_get_album_tracks(browse_id: str):
    """Get all tracks for an album."""
    from musicload.search import get_album_tracks
    import logging

    logger = logging.getLogger(__name__)

    cache_key = f"album_tracks:{browse_id}"
    cached = _album_tracks_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        tracks = get_album_tracks(browse_id)
        if not tracks:
            raise HTTPException(status_code=404, detail="No tracks found for this album")

        response = AlbumTracksResponse(
            browse_id=browse_id,
            album_title=tracks[0].album if tracks else "Unknown Album",
            tracks=[_track_to_response(track) for track in tracks],
        )
        _album_tracks_cache.put(cache_key, response)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get album tracks: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/download", response_model=DownloadResponse)
async def api_download(request: DownloadRequest, http_request: Request):
    """Download a track by video ID."""
    config = get_config()

    # Validate format
    valid_formats = ['opus', 'mp3', 'flac']
    audio_format = request.audio_format.lower()
    if audio_format not in valid_formats:
        return DownloadResponse(
            success=False,
            message=f"Invalid format. Must be one of: {', '.join(valid_formats)}",
        )

    try:
        audio_path = download(
            video_id=request.video_id,
            output_dir=config.download_dir,
            audio_format=audio_format,
            filename_template=config.filename_template,
            fetch_lyrics=True,
            organization_mode=config.organization_mode,
            use_primary_artist=config.use_primary_artist,
            cookie_file=config.cookie_file_path,
            artists=request.artists,
        )

        return DownloadResponse(
            success=True,
            message=f"Downloaded: {request.title} - {request.artist} ({audio_format.upper()})",
            file_path=str(audio_path) if audio_path else None,
            file_name=audio_path.name if audio_path else None,
        )

    except Exception as e:
        return DownloadResponse(
            success=False,
            message=f"Download failed: {str(e)}",
        )


@app.get("/api/download-file/{file_path:path}")
async def download_file(file_path: str):
    """Serve downloaded file for browser download."""
    config = get_config()
    file_path = urllib.parse.unquote(file_path)
    requested_path = Path(file_path)

    try:
        # Normalize relative paths from different callers:
        # - M3U entries: "Artist - Song.opus"
        # - Queue jobs in some setups: "downloads/Artist - Song.opus"
        # Both should resolve to config.download_dir / "<entry>"
        if not requested_path.is_absolute():
            download_dir_name = config.download_dir.name
            if requested_path.parts and requested_path.parts[0] == download_dir_name:
                requested_path = Path(*requested_path.parts[1:])
            requested_path = config.download_dir / requested_path

        abs_requested = requested_path.resolve()
        abs_download_dir = config.download_dir.resolve()

        # Security: ensure path is within download_dir
        try:
            abs_requested.relative_to(abs_download_dir)
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")

        if not abs_requested.exists():
            raise HTTPException(status_code=404, detail="File not found")

        if not abs_requested.is_file():
            raise HTTPException(status_code=400, detail="Not a file")

        return FileResponse(
            path=abs_requested,
            filename=abs_requested.name,
            media_type='application/octet-stream'
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to serve file")


@app.get("/api/stream-url/{video_id}", response_model=StreamUrlResponse)
async def get_stream_url(video_id: str):
    """Get direct stream URL for a video using yt-dlp."""
    config = get_config()
    try:
        youtube_url = f"https://music.youtube.com/watch?v={video_id}"
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
        }

        info = extract_info_with_retry(
            ydl_opts=ydl_opts,
            url=youtube_url,
            download=False,
            cookie_file=config.cookie_file_path,
            config=config,
        )

        # Extract direct audio URL
        if 'url' in info:
            stream_url = info['url']
        elif 'formats' in info:
            audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
            if audio_formats:
                audio_formats.sort(key=lambda f: f.get('abr', 0), reverse=True)
                stream_url = audio_formats[0]['url']
            else:
                raise HTTPException(status_code=404, detail="No audio stream found")
        else:
            raise HTTPException(status_code=404, detail="No stream URL available")

        is_hls = info.get('protocol', '') == 'm3u8_native' or '.m3u8' in stream_url

        return StreamUrlResponse(
            video_id=video_id,
            url=stream_url,
            expires_in=21600,
            is_hls=is_hls,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stream URL: {str(e)}")


@app.get("/api/preview/{video_id}")
async def preview_audio(video_id: str):
    """Stream audio through the server to avoid CORS issues with HLS streams.

    Uses yt-dlp to resolve the stream URL, then ffmpeg to remux HLS into
    a fragmented MP4 that the browser can play progressively.
    """
    if not re.match(r'^[a-zA-Z0-9_-]{11}$', video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")

    config = get_config()
    youtube_url = f"https://music.youtube.com/watch?v={video_id}"

    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
        }
        info = extract_info_with_retry(
            ydl_opts=ydl_opts,
            url=youtube_url,
            download=False,
            cookie_file=config.cookie_file_path,
            config=config,
        )

        if 'url' in info:
            stream_url = info['url']
        elif 'formats' in info:
            audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
            if audio_formats:
                audio_formats.sort(key=lambda f: f.get('abr', 0), reverse=True)
                stream_url = audio_formats[0]['url']
            else:
                raise HTTPException(status_code=404, detail="No audio stream found")
        else:
            raise HTTPException(status_code=404, detail="No stream URL available")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stream URL: {str(e)}")

    # Convert HLS stream to MP3 via ffmpeg for browser playback
    cmd = [
        'ffmpeg',
        '-i', stream_url,
        '-vn',
        '-f', 'mp3',
        '-ab', '128k',
        '-loglevel', 'error',
        'pipe:1',
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def stream_audio():
        try:
            while True:
                chunk = await process.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            if process.returncode is None:
                process.kill()

    return StreamingResponse(stream_audio(), media_type="audio/mpeg")


# Queue endpoints


class QueueAddRequest(BaseModel):
    """Request to add a job to the queue."""

    video_id: str
    title: str
    artist: str
    artists: list[str] | None = None
    album: str | None = None
    audio_format: str = "opus"


class QueueAddAlbumRequest(BaseModel):
    """Request to add an album to the queue."""

    browse_id: str
    album_title: str
    artist: str
    album_year: int | None = None
    audio_format: str = "opus"


class QueueAddResponse(BaseModel):
    """Response after adding a job."""

    job_id: str
    status: str


@app.post("/api/queue/add", response_model=QueueAddResponse)
async def add_to_queue(request: QueueAddRequest, http_request: Request):
    """Add a download job to the queue."""
    if not queue_manager:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")

    # Validate format
    valid_formats = ["opus", "mp3", "flac"]
    audio_format = request.audio_format.lower()
    if audio_format not in valid_formats:
        raise HTTPException(
            status_code=400, detail=f"Invalid format. Must be one of: {', '.join(valid_formats)}"
        )

    config = get_config()
    job_id = await queue_manager.add_job(
        video_id=request.video_id,
        title=request.title,
        artist=request.artist,
        format=audio_format,
        artists=request.artists,
        album=request.album,
    )

    return QueueAddResponse(job_id=job_id, status="queued")


@app.post("/api/queue/add-album")
async def add_album_to_queue(request: QueueAddAlbumRequest, http_request: Request):
    """Add all tracks from an album to the download queue."""
    if not queue_manager:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")

    from musicload.search import get_album_tracks
    import logging

    logger = logging.getLogger(__name__)

    try:
        tracks = get_album_tracks(request.browse_id)
        if not tracks:
            raise HTTPException(status_code=404, detail="No tracks found for this album")

        # Validate format
        valid_formats = ["opus", "mp3", "flac"]
        audio_format = request.audio_format.lower()
        if audio_format not in valid_formats:
            raise HTTPException(
                status_code=400, detail=f"Invalid format. Must be one of: {', '.join(valid_formats)}"
            )

        config = get_config()
        job_ids = []
        for track_number, track in enumerate(tracks, start=1):
            job_id = await queue_manager.add_job(
                video_id=track.video_id,
                title=track.title,
                artist=track.artist,
                format=audio_format,
                artists=track.artists,
                album=request.album_title,
                album_artist=request.artist,
                album_year=request.album_year,
                track_number=track_number,
            )
            job_ids.append(job_id)

        logger.info("Queued %d tracks from album: %s", len(job_ids), request.album_title)
        return {
            "job_ids": job_ids,
            "track_count": len(job_ids),
            "message": f"Added {len(job_ids)} tracks to queue"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to queue album: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/queue/jobs")
async def list_queue_jobs():
    """List all jobs in the queue."""
    if not queue_manager:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")

    jobs = await queue_manager.list_jobs()
    return {"jobs": [job.to_dict() for job in jobs]}


@app.delete("/api/queue/{job_id}")
async def remove_queue_job(job_id: str):
    """Remove or clear a job from the queue."""
    if not queue_manager:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")

    success = await queue_manager.remove_job(job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Job not found or cannot be removed")

    return {"success": True}


@app.post("/api/queue/cancel-all")
async def cancel_all_queue_jobs():
    """Cancel every queued or active download."""
    if not queue_manager:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")
    cancelled = await queue_manager.cancel_all()
    return {"success": True, "cancelled": cancelled}


@app.get("/api/queue/stream")
async def stream_queue_updates(request: Request):
    """Server-Sent Events endpoint for real-time queue updates."""
    if not queue_manager:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")

    async def event_generator():
        """Generate SSE events for queue updates."""
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                # Get current jobs
                jobs = await queue_manager.list_jobs()
                jobs_data = [job.to_dict() for job in jobs]

                # Send update via SSE
                yield f"data: {json.dumps(jobs_data)}\n\n"

                # Wait before next update
                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/queue/stats")
async def get_queue_stats():
    """Get queue statistics."""
    if not queue_manager:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")

    return await queue_manager.get_stats()


# Explore endpoints

@app.get("/api/explore/moods")
async def api_explore_moods():
    """Get mood & genre categories."""
    from musicload.search import get_mood_categories
    import logging
    logger = logging.getLogger(__name__)

    cached = _moods_cache.get("moods")
    if cached is not None:
        return cached

    try:
        sections = get_mood_categories()
        result = [
            MoodSectionResponse(
                title=s.title,
                categories=[MoodCategoryResponse(title=c.title, params=c.params) for c in s.categories],
            )
            for s in sections
        ]
        _moods_cache.put("moods", result)
        return result
    except Exception as e:
        logger.error("Failed to get mood categories: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to get mood categories: {str(e)}")


@app.get("/api/explore/mood-playlists")
async def api_explore_mood_playlists(params: str = Query(..., description="Category params from moods endpoint")):
    """Get playlists for a mood/genre category."""
    from musicload.search import get_mood_playlists
    import logging
    logger = logging.getLogger(__name__)

    cache_key = f"mood_playlists:{params}"
    cached = _mood_playlists_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        playlists = get_mood_playlists(params)
        result = [
            MoodPlaylistResponse(
                playlist_id=p.playlist_id,
                title=p.title,
                thumbnail_url=p.thumbnail_url,
                author=p.author,
            )
            for p in playlists
        ]
        _mood_playlists_cache.put(cache_key, result)
        return result
    except Exception as e:
        logger.error("Failed to get mood playlists: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to get mood playlists: {str(e)}")


@app.get("/api/explore/charts")
async def api_explore_charts(country: str = Query("ZZ", description="ISO 3166-1 Alpha-2 country code")):
    """Get current music charts."""
    from musicload.search import get_charts
    import logging
    logger = logging.getLogger(__name__)

    # Validate country code format
    if not re.match(r"^[A-Z]{2}$", country):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid country code '{country}': must be a 2-letter uppercase ISO 3166-1 Alpha-2 code (e.g., 'US', 'GB', 'ZZ')"
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
                    video_id=t.video_id,
                    title=t.title,
                    artist=t.artist,
                    artists=t.artists,
                    album=t.album,
                    thumbnail_url=t.thumbnail_url,
                    rank=t.rank,
                    trend=t.trend,
                    view_count=t.view_count,
                    duration=t.duration_display,
                    video_type=t.video_type,
                )
                for t in charts.tracks
            ],
            artists=[
                ChartArtistResponse(
                    browse_id=a.browse_id,
                    title=a.title,
                    thumbnail_url=a.thumbnail_url,
                    rank=a.rank,
                    trend=a.trend,
                )
                for a in charts.artists
            ],
        )
        _charts_cache.put(cache_key, response)
        return response
    except Exception as e:
        logger.error("Failed to get charts: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to get charts: {str(e)}")


@app.get("/api/explore/new-releases")
async def api_explore_new_releases():
    """Get new album releases from YouTube Music."""
    from musicload.search import get_new_releases
    import logging
    logger = logging.getLogger(__name__)

    cached = _new_releases_cache.get("new_releases")
    if cached is not None:
        return cached

    try:
        albums = get_new_releases()
        result = [
            AlbumResponse(**album.model_dump())
            for album in albums
        ]
        _new_releases_cache.put("new_releases", result)
        return result
    except Exception as e:
        logger.error("Failed to get new releases: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to get new releases: {str(e)}")


@app.get("/api/explore/playlist/{playlist_id}/tracks")
async def api_explore_playlist_tracks(playlist_id: str):
    """Get tracks from a YouTube Music playlist (mood/genre playlist)."""
    from musicload.search import get_playlist_tracks
    import logging
    logger = logging.getLogger(__name__)

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
    except ValueError as e:
        logger.warning("Playlist unavailable: %s", e)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to get playlist tracks: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to get playlist tracks: {str(e)}")


@app.get("/api/image-proxy")
async def api_image_proxy(url: str = Query(..., description="Image URL to proxy")):
    """Proxy and cache images from allowed hosts to prevent 429 errors."""
    import logging

    logger = logging.getLogger(__name__)

    validated = validate_image_url(url)
    if validated is None:
        raise HTTPException(status_code=400, detail="URL not allowed")

    if not _image_proxy:
        raise HTTPException(status_code=500, detail="Image proxy not initialized")

    try:
        data, content_type = await _image_proxy.fetch(validated)
    except Exception as e:
        logger.warning("Image proxy fetch failed for '%s': %s", url, e)
        raise HTTPException(status_code=502, detail="Failed to fetch image")

    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# Cookie management endpoints
@app.post("/api/settings/cookies/upload")
async def upload_cookies(file: UploadFile = File(...)):
    """Upload cookies.txt file for yt-dlp authentication."""
    # Validate file
    if not file.filename.endswith('.txt'):
        raise HTTPException(status_code=400, detail="File must be a .txt file")

    # Read file content
    content = await file.read()
    if len(content) > 1024 * 1024:  # 1MB limit
        raise HTTPException(status_code=400, detail="File too large (max 1MB)")

    # Validate file encoding and content
    try:
        content_str = content.decode('utf-8')
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text")

    # Validate Netscape cookie file format
    lines = content_str.strip().split('\n')
    has_valid_cookies = False

    for line in lines:
        # Skip empty lines and comments
        if not line.strip() or line.startswith('#'):
            continue

        # Cookie lines should have 7 tab-separated fields
        parts = line.split('\t')
        if len(parts) == 7:
            has_valid_cookies = True
            break

    if not has_valid_cookies:
        raise HTTPException(
            status_code=400,
            detail="Invalid cookie file format. Expected Netscape format with tab-separated values"
        )

    # Write cookie file to data directory
    config = get_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)

    cookie_path = config.data_dir / "cookies.txt"
    cookie_path.write_bytes(content)
    cookie_path.chmod(0o600)  # Secure permissions

    return {
        "success": True,
        "message": "Cookie file uploaded successfully",
        "path": str(cookie_path)
    }


@app.get("/api/settings/cookies/status")
async def get_cookie_status():
    """Check if cookies are configured."""
    config = get_config()
    cookie_path = config.cookie_file_path

    if cookie_path:
        path = Path(cookie_path)
        return {
            "configured": True,
            "source": "uploaded" if "cookies.txt" in cookie_path and str(config.data_dir) in cookie_path else "environment",
            "path": cookie_path,
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0
        }
    else:
        return {
            "configured": False,
            "source": None,
            "path": None,
            "exists": False
        }


@app.delete("/api/settings/cookies")
async def delete_cookies():
    """Delete uploaded cookie file."""
    cookie_path = get_config().data_dir / "cookies.txt"
    if cookie_path.exists():
        cookie_path.unlink()
        return {"success": True, "message": "Cookie file deleted"}
    else:
        raise HTTPException(status_code=404, detail="No uploaded cookie file found")


# Playlist (Downloads tab) endpoints


def _extract_track_info(entry_path: str, download_dir: Path) -> dict:
    """Extract track metadata from an audio file using mutagen, with path-based fallback."""
    full_path = download_dir / entry_path
    file_exists = full_path.exists()

    info = {
        "entry_path": entry_path,
        "title": "",
        "artist": "",
        "album": None,
        "duration": None,
        "file_exists": file_exists,
    }

    # Try mutagen metadata extraction
    if file_exists:
        try:
            from mutagen import File as MutagenFile

            audio = MutagenFile(full_path)
            if audio:
                title = audio.get("title", [])
                artist = audio.get("artist", []) or audio.get("ARTISTS", []) or audio.get("artists", [])
                album = audio.get("album", [])

                info["title"] = str(title[0]) if isinstance(title, list) and title else str(title) if title else ""
                info["artist"] = str(artist[0]) if isinstance(artist, list) and artist else str(artist) if artist else ""
                info["album"] = str(album[0]) if isinstance(album, list) and album else str(album) if album else None

                if audio.info and hasattr(audio.info, "length") and audio.info.length:
                    total_seconds = int(audio.info.length)
                    minutes = total_seconds // 60
                    seconds = total_seconds % 60
                    info["duration"] = f"{minutes}:{seconds:02d}"
        except Exception:
            pass

    # Fallback to path parsing if metadata is incomplete
    if not info["title"]:
        title, artist = _parse_track_info_from_path(entry_path)
        info["title"] = title
        if not info["artist"]:
            info["artist"] = artist

    return info


def _parse_track_info_from_path(entry_path: str) -> tuple[str, str]:
    """Parse title and artist from file path.

    Handles:
      - Flat mode: "Artist - Title.opus"
      - Album mode: "Artist/Album/01 - Title.opus"
    """
    p = Path(entry_path)
    stem = p.stem
    parts = p.parts

    # Album mode: Artist/Album/TrackNum - Title.ext
    if len(parts) >= 3:
        artist = parts[0]
        # Strip leading track number pattern like "01 - "
        title = stem
        if " - " in title:
            title = title.split(" - ", 1)[1]
        return title, artist

    # Flat mode: Artist - Title.ext
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip(), artist.strip()

    return stem, ""


@app.get("/api/playlist/status")
async def api_playlist_status(http_request: Request):
    """Check if playlist feature is enabled for the current user."""
    config = get_config()
    remote_user = _get_remote_user(http_request, config)
    playlist_name = config.effective_playlist_name(remote_user)
    return {"enabled": playlist_name is not None}


@app.get("/api/playlist/tracks")
async def api_playlist_tracks(http_request: Request):
    """List tracks in the user's download playlist with metadata."""
    import asyncio
    import logging

    logger = logging.getLogger(__name__)
    config = get_config()
    remote_user = _get_remote_user(http_request, config)
    playlist_name = config.effective_playlist_name(remote_user)

    if not playlist_name:
        raise HTTPException(status_code=404, detail="Playlist not configured")

    entries = read_m3u(playlist_name, config.download_dir)
    loop = asyncio.get_event_loop()

    # Extract metadata in thread pool to avoid blocking the event loop
    tracks = []
    for entry in entries:
        info = await loop.run_in_executor(None, _extract_track_info, entry, config.download_dir)
        tracks.append(info)

    return {"tracks": tracks, "total": len(tracks), "playlist_name": playlist_name}


@app.delete("/api/playlist/tracks")
async def api_playlist_remove_track(entry_path: str = Query(..., description="Exact M3U entry to remove"), http_request: Request = None):
    """Remove a track entry from the playlist (does not delete the audio file)."""
    config = get_config()
    remote_user = _get_remote_user(http_request, config)
    playlist_name = config.effective_playlist_name(remote_user)

    if not playlist_name:
        raise HTTPException(status_code=404, detail="Playlist not configured")

    removed = remove_from_m3u(entry_path, playlist_name, config.download_dir)
    if not removed:
        raise HTTPException(status_code=404, detail="Entry not found in playlist")

    return {"success": True, "message": "Track removed from playlist"}


# Local library (Files tab) endpoints


def _scan_library_files(download_dir: Path) -> list[Path]:
    """Recursively find audio files in download_dir, newest first."""
    from musicload.tagging import SUPPORTED_EXTENSIONS

    files = [
        p
        for p in download_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _resolve_library_path(entry_path: str, download_dir: Path) -> Path:
    """Resolve and validate a library entry path against directory traversal."""
    requested_path = Path(entry_path)
    if requested_path.is_absolute():
        raise HTTPException(status_code=403, detail="Access denied")

    abs_requested = (download_dir / requested_path).resolve()
    abs_download_dir = download_dir.resolve()
    try:
        abs_requested.relative_to(abs_download_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    return abs_requested


@app.get("/api/library/files", response_model=LibraryTracksResponse)
async def api_library_files(
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200),
):
    """List local audio files on disk, most recently added first."""
    config = get_config()
    download_dir = config.download_dir
    loop = asyncio.get_event_loop()

    all_files = await loop.run_in_executor(None, _scan_library_files, download_dir)
    # Metadata is read before paging so a local search also finds artists, albums,
    # and titles that do not appear in the filename.
    query = q.strip().casefold()
    matching_tracks = []
    for f in all_files:
        rel_path = str(f.relative_to(download_dir))
        info = await loop.run_in_executor(None, _extract_track_info, rel_path, download_dir)
        stat = f.stat()
        track = LibraryTrackResponse(
            entry_path=rel_path,
            title=info["title"] or f.stem,
            artist=info["artist"],
            album=info["album"],
            duration=info["duration"],
            file_size=stat.st_size,
            modified_at=stat.st_mtime,
        )
        searchable = " ".join(filter(None, [track.entry_path, track.title, track.artist, track.album])).casefold()
        if not query or query in searchable:
            matching_tracks.append(track)

    total = len(matching_tracks)
    tracks = matching_tracks[offset : offset + limit]

    return LibraryTracksResponse(tracks=tracks, total=total, limit=limit, offset=offset)


@app.get("/api/library/play")
async def api_library_play_file(entry_path: str = Query(..., description="Relative path of the audio file")):
    """Stream a local audio file for playback in the browser."""
    config = get_config()
    abs_path = _resolve_library_path(entry_path, config.download_dir)
    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    from musicload.tagging import SUPPORTED_EXTENSIONS
    if abs_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Not an audio file")
    return FileResponse(path=abs_path, filename=abs_path.name)


@app.get("/api/library/thumbnail")
async def api_library_thumbnail(entry_path: str = Query(..., description="Relative path of the audio file")):
    """Return embedded album artwork, when the downloaded file has it."""
    config = get_config()
    abs_path = _resolve_library_path(entry_path, config.download_dir)
    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(abs_path)
        if audio is None or not audio.tags:
            raise HTTPException(status_code=404, detail="No embedded cover")
        for tag in audio.tags.values():
            if hasattr(tag, "data") and getattr(tag, "mime", "").startswith("image/"):
                return Response(content=tag.data, media_type=tag.mime)
        for key in ("covr", "metadata_block_picture"):
            covers = audio.tags.get(key)
            if covers:
                cover = covers[0] if isinstance(covers, list) else covers
                return Response(content=bytes(cover), media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="No embedded cover")


@app.delete("/api/library/files")
async def api_library_delete_file(
    entry_path: str = Query(..., description="Relative path of the file to delete"),
):
    """Delete a local audio file from disk."""
    import logging

    logger = logging.getLogger(__name__)

    config = get_config()
    download_dir = config.download_dir
    abs_path = _resolve_library_path(entry_path, download_dir)

    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    from musicload.tagging import SUPPORTED_EXTENSIONS

    if abs_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Not an audio file")

    try:
        abs_path.unlink()
    except Exception as e:
        logger.error("Failed to delete file %s: %s", abs_path, e)
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")

    rel_entry = str(abs_path.relative_to(download_dir))
    # Jetzt leeren Elternordner aufraeumen (Album-Modus)
    try:
        parent = abs_path.parent
        if parent != download_dir and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception:
        pass

    logger.info("Deleted library file: %s", rel_entry)
    return {"success": True, "message": f"Deleted: {rel_entry}"}
