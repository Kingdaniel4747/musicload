"""FastAPI web application for Musicload."""

import asyncio
import html
import json
import logging
import mimetypes
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import JSONResponse, RedirectResponse

from musicload import __version__
from musicload.config import get_config
from musicload.playlist import add_to_m3u, read_m3u, remove_from_m3u
from musicload.queue import QueueManager
from musicload.search import search
from musicload.web.api_cache import TtlCache
from musicload.web.artwork import embedded_artwork, folder_artwork
from musicload.web.image_proxy import ImageProxyService, validate_image_url
from musicload.web.responses import sse_event as _sse_event
from musicload.web.routes.explore import router as explore_router
from musicload.web.routes.search import router as search_router
from musicload.web.schemas import (
    AppSettingsRequest,
    DownloadRequest,
    DownloadResponse,
    LibraryTrackResponse,
    LibraryTracksResponse,
    ListenBrainzSettingsRequest,
    LoginRequest,
    QueueAddAlbumRequest,
    QueueAddRequest,
    QueueAddResponse,
    StreamUrlResponse,
)
from musicload.web.schemas import (
    track_to_response as _track_to_response,
)
from musicload.web.settings_service import (
    build_settings_values,
)
from musicload.web.settings_service import (
    settings_response as _settings_response,
)

app = FastAPI(title="Musicload", description="Search and download music from YouTube Music")
logger = logging.getLogger(__name__)

# Configure CORS
config = get_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=config.cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global queue manager
queue_manager: QueueManager | None = None
_background_tasks: set[asyncio.Task] = set()
_library_metadata_cache: dict[str, tuple[float, int, dict]] = {}

# Global image proxy service
_image_proxy: ImageProxyService | None = None

# API response cache (TTL in seconds)
_stream_url_cache = TtlCache(max_entries=50, ttl_seconds=300)      # 5 min

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


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Apply low-risk browser hardening headers to every response."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=()",
    )
    return response


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
    for coroutine in (
        _warm_library_cache(),
        _warm_listenbrainz_caches(),
        _listenbrainz_scheduler(),
    ):
        task = asyncio.create_task(coroutine)
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


@app.on_event("shutdown")
async def shutdown_event():
    """Stop queue manager and image proxy on shutdown."""
    if queue_manager:
        await queue_manager.stop()
    if _image_proxy:
        await _image_proxy.close()
    for task in list(_background_tasks):
        task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main search page."""
    is_admin = not config.navidrome_url or bool(request.session.get("is_admin"))
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "version": __version__,
            "auth_enabled": bool(config.navidrome_url),
            "auth_user": request.session.get("username") if config.navidrome_url else None,
            "is_admin": is_admin,
            "can_view_logs": is_admin,
            "listenbrainz_enabled": config.listenbrainz_web,
            "default_audio_format": config.audio_format,
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


def _current_account_name(request: Request) -> str:
    """Return the stable local account key used for per-user web settings."""
    if config.navidrome_url:
        username = request.session.get("username")
        if not username:
            raise HTTPException(status_code=401, detail="Authentication required")
        return str(username)
    return "local"


def _require_listenbrainz_web() -> None:
    if not config.listenbrainz_web:
        raise HTTPException(status_code=404, detail="ListenBrainz web explorer is disabled")


@app.get("/api/listenbrainz/settings")
async def api_listenbrainz_settings(request: Request):
    """Return the ListenBrainz username for the current Musicload account."""
    _require_listenbrainz_web()
    from musicload.web.listenbrainz_settings import get_listenbrainz_settings

    settings = await asyncio.to_thread(
        get_listenbrainz_settings,
        config.data_dir,
        _current_account_name(request),
    )
    return settings or {
        "username": None,
        "auto_download": False,
        "download_weekday": 0,
        "download_time": "03:00",
        "timezone": "UTC",
        "last_run_date": None,
    }


@app.put("/api/listenbrainz/settings")
async def api_save_listenbrainz_settings(
    settings: ListenBrainzSettingsRequest, request: Request
):
    """Save a ListenBrainz username for the current Musicload account."""
    _require_listenbrainz_web()
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from musicload.web.listenbrainz_settings import set_listenbrainz_settings

    username = settings.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="ListenBrainz username is required")
    try:
        ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError as error:
        raise HTTPException(status_code=400, detail="Invalid timezone") from error
    account_name = _current_account_name(request)
    await asyncio.to_thread(
        set_listenbrainz_settings,
        config.data_dir,
        account_name,
        {
            "username": username,
            "auto_download": settings.auto_download,
            "download_weekday": settings.download_weekday,
            "download_time": settings.download_time,
            "timezone": settings.timezone,
        },
    )
    if settings.auto_download:
        await asyncio.to_thread(
            add_to_m3u,
            [],
            _listenbrainz_playlist_name(account_name),
            config.download_dir,
        )
    return {
        "success": True,
        "username": username,
        "auto_download": settings.auto_download,
        "download_weekday": settings.download_weekday,
        "download_time": settings.download_time,
        "timezone": settings.timezone,
    }


def _fetch_listenbrainz_songs(username: str) -> list:
    from musicload.plugins.base import PluginConfig
    from musicload.plugins.listenbrainz import ListenbrainzPlugin

    plugin_config = PluginConfig(
        name="web-listenbrainz",
        download_dir=config.download_dir,
        audio_format=config.audio_format,
        filename_template=config.filename_template,
        organization_mode=config.organization_mode,
        use_primary_artist=config.use_primary_artist,
        config={
            "user": username,
            "recommendation_type": "weekly-exploration",
            "timeout": 15,
        },
    )
    try:
        return ListenbrainzPlugin().fetch_songs(plugin_config)
    except Exception as error:
        if "Could not find <content>" in str(error) or "404" in str(error):
            return []
        raise


async def _match_listenbrainz_song(index: int, song, semaphore: asyncio.Semaphore):
    try:
        async with semaphore:
            matches = await asyncio.to_thread(search, song.search_query, 1)
    except Exception as error:
        logger.warning("ListenBrainz match failed for %s: %s", song.search_query, error)
        matches = []
    result = _track_to_response(matches[0]).model_dump() if matches else None
    return index, result


async def _match_listenbrainz_songs(
    songs: list,
    progress_callback: Callable[[dict], Awaitable[None]] | None,
) -> list[dict]:
    total = len(songs)
    processed = 0
    indexed_results: list[tuple[int, dict]] = []
    semaphore = asyncio.Semaphore(4)
    tasks = [
        asyncio.create_task(_match_listenbrainz_song(index, song, semaphore))
        for index, song in enumerate(songs)
    ]
    try:
        for completed in asyncio.as_completed(tasks):
            index, result = await completed
            processed += 1
            if result is not None:
                indexed_results.append((index, result))
            if progress_callback:
                await progress_callback(
                    {
                        "processed": processed,
                        "total": total,
                        "matched": len(indexed_results),
                        "percent": round(processed * 100 / total) if total else 100,
                    }
                )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    return [result for _, result in sorted(indexed_results)]


async def _refresh_listenbrainz_recommendations(
    username: str,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Fetch, match, and persist one user's weekly recommendations."""
    from musicload.web.listenbrainz_settings import set_cached_recommendations

    songs = await asyncio.to_thread(_fetch_listenbrainz_songs, username)
    results = await _match_listenbrainz_songs(songs, progress_callback)
    payload = {
        "playlist_exists": bool(songs),
        "playlist_title": "Weekly Exploration",
        "results": results,
        "total": len(results),
    }
    await asyncio.to_thread(
        set_cached_recommendations, config.data_dir, username, payload
    )
    return payload


def _listenbrainz_playlist_name(account_name: str) -> str:
    """Return a filesystem-safe per-account playlist name."""
    safe_account = re.sub(r"[^A-Za-z0-9._-]+", "-", account_name).strip("-._")
    return f"ListenBrainz Weekly - {safe_account or 'user'}"


@app.get("/api/listenbrainz/recommendations/stream")
async def api_listenbrainz_recommendations_stream(request: Request):
    """Return cached recommendations immediately or populate the cache once."""
    _require_listenbrainz_web()
    from musicload.web.listenbrainz_settings import (
        get_cached_recommendations,
        get_listenbrainz_username,
    )

    account_name = _current_account_name(request)

    async def event_generator():
        username = await asyncio.to_thread(
            get_listenbrainz_username, config.data_dir, account_name
        )
        if not username:
            yield _sse_event("failure", {"message": "No ListenBrainz username configured"})
            return
        payload = await asyncio.to_thread(
            get_cached_recommendations, config.data_dir, username
        )
        if payload is None:
            progress_queue: asyncio.Queue[dict] = asyncio.Queue()
            refresh_task = asyncio.create_task(
                _refresh_listenbrainz_recommendations(username, progress_queue.put)
            )
            try:
                while not refresh_task.done():
                    try:
                        progress = await asyncio.wait_for(progress_queue.get(), timeout=10)
                        yield _sse_event("progress", progress)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                while not progress_queue.empty():
                    yield _sse_event("progress", progress_queue.get_nowait())
                payload = await refresh_task
            except asyncio.CancelledError:
                refresh_task.cancel()
                await asyncio.gather(refresh_task, return_exceptions=True)
                raise
            except Exception as error:
                logger.warning("ListenBrainz web fetch failed for %s: %s", username, error)
                yield _sse_event("failure", {"message": str(error)})
                return
        yield _sse_event("complete", payload)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _warm_listenbrainz_caches() -> None:
    """Refresh only stale ListenBrainz caches, then sleep for six hours."""
    if not config.listenbrainz_web:
        return
    from datetime import UTC, datetime, timedelta

    from musicload.web.listenbrainz_settings import (
        get_cached_recommendations,
        list_listenbrainz_settings,
    )

    while True:
        settings = await asyncio.to_thread(list_listenbrainz_settings, config.data_dir)
        for item in settings:
            try:
                cached = await asyncio.to_thread(
                    get_cached_recommendations, config.data_dir, item["username"]
                )
                if cached and cached.get("cached_at"):
                    try:
                        cached_at = datetime.fromisoformat(cached["cached_at"])
                        if datetime.now(UTC) - cached_at < timedelta(hours=6):
                            continue
                    except (TypeError, ValueError):
                        logger.info("Ignoring invalid ListenBrainz cache timestamp")
                await _refresh_listenbrainz_recommendations(item["username"])
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("ListenBrainz cache refresh failed for %s: %s", item["username"], error)
        await asyncio.sleep(6 * 60 * 60)


def _listenbrainz_due_date(item: dict) -> str | None:
    from datetime import datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        now = datetime.now(ZoneInfo(item["timezone"]))
    except ZoneInfoNotFoundError:
        logger.warning("Invalid ListenBrainz timezone for %s", item["account_name"])
        return None
    local_date = now.date().isoformat()
    if item["last_run_date"] == local_date:
        return None
    if now.weekday() != item["download_weekday"]:
        return None
    if now.strftime("%H:%M") < item["download_time"]:
        return None
    return local_date


async def _scheduled_listenbrainz_payload(item: dict) -> dict | None:
    from musicload.web.listenbrainz_settings import get_cached_recommendations

    try:
        return await _refresh_listenbrainz_recommendations(item["username"])
    except Exception:
        logger.warning(
            "Scheduled ListenBrainz refresh failed for %s; using cache",
            item["username"],
            exc_info=True,
        )
        return await asyncio.to_thread(
            get_cached_recommendations, config.data_dir, item["username"]
        )


async def _queue_listenbrainz_tracks(item: dict, tracks: list[dict]) -> None:
    if not queue_manager:
        return
    playlist_name = _listenbrainz_playlist_name(item["account_name"])
    active_ids = {
        job.video_id
        for job in await queue_manager.list_jobs()
        if job.status.value in {"queued", "downloading"}
    }
    for track in tracks:
        if track["video_id"] in active_ids:
            continue
        await queue_manager.add_job(
            video_id=track["video_id"],
            title=track["title"],
            artist=track["artist"],
            format=config.audio_format,
            artists=track.get("artists") or [],
            album=track.get("album"),
            playlist_name=playlist_name,
        )
        active_ids.add(track["video_id"])


async def _run_listenbrainz_schedule(item: dict) -> None:
    import hashlib

    from musicload.web.listenbrainz_settings import mark_listenbrainz_run

    local_date = _listenbrainz_due_date(item)
    if local_date is None:
        return
    payload = await _scheduled_listenbrainz_payload(item)
    if payload is None:
        return
    tracks = payload.get("results") or []
    playlist_hash = hashlib.sha256(
        "\n".join(track["video_id"] for track in tracks).encode("utf-8")
    ).hexdigest()
    if tracks and playlist_hash != item.get("last_download_hash"):
        await _queue_listenbrainz_tracks(item, tracks)
    await asyncio.to_thread(
        mark_listenbrainz_run,
        config.data_dir,
        item["account_name"],
        local_date,
        playlist_hash,
    )


async def _listenbrainz_scheduler() -> None:
    """Queue each account's new cached playlist at its optional local time."""
    if not config.listenbrainz_web:
        return
    from musicload.web.listenbrainz_settings import list_listenbrainz_settings

    poll_seconds = 60
    while True:
        try:
            await asyncio.sleep(poll_seconds)
            if not queue_manager:
                continue
            settings = await asyncio.to_thread(list_listenbrainz_settings, config.data_dir)
            poll_seconds = 60 if any(item["auto_download"] for item in settings) else 300
            for item in settings:
                if item["auto_download"]:
                    await _run_listenbrainz_schedule(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ListenBrainz scheduler iteration failed")


def _is_admin(request: Request) -> bool:
    """Treat authentication-disabled installations as fully administrative."""
    return not config.navidrome_url or bool(request.session.get("is_admin"))


def _require_admin(request: Request) -> None:
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")


@app.get("/api/logs/{source}")
async def api_logs(
    source: str,
    request: Request,
    offset: int = Query(0, ge=0),
):
    """Return raw incremental web log output."""
    if config.navidrome_url and not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Administrator access required")
    if source != "web":
        raise HTTPException(status_code=404, detail="Unknown log source")

    from musicload.web.logs import read_log_chunk

    return {
        "source": source,
        **read_log_chunk(config.data_dir / "logs" / f"{source}.log", offset),
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


# Search and album endpoints
app.include_router(search_router)


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
        from musicload.download import download

        audio_path = await asyncio.to_thread(
            download,
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


def _resolve_downloaded_file_path(file_path: str, download_dir: Path) -> Path:
    """Resolve an absolute or relative download path and prevent traversal."""
    requested_path = Path(urllib.parse.unquote(file_path))
    if not requested_path.is_absolute():
        if requested_path.parts and requested_path.parts[0] == download_dir.name:
            requested_path = Path(*requested_path.parts[1:])
        requested_path = download_dir / requested_path

    abs_requested = requested_path.resolve()
    try:
        abs_requested.relative_to(download_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    return abs_requested


def _delete_downloaded_audio_file(file_path: str) -> str:
    """Delete one validated audio file and return its relative library path."""
    from musicload.tagging import SUPPORTED_EXTENSIONS

    download_dir = get_config().download_dir.resolve()
    abs_path = _resolve_downloaded_file_path(file_path, download_dir)
    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if abs_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Not an audio file")

    try:
        abs_path.unlink()
    except OSError as error:
        logger.error("Failed to delete file %s: %s", abs_path, error)
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {error}")

    parent = abs_path.parent
    try:
        if parent != download_dir and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
    relative_path = str(abs_path.relative_to(download_dir))
    _library_metadata_cache.pop(relative_path, None)
    return relative_path


@app.get("/api/download-file/{file_path:path}")
async def download_file(file_path: str):
    """Serve downloaded file for browser download."""
    config = get_config()

    try:
        abs_requested = _resolve_downloaded_file_path(file_path, config.download_dir)

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
    except Exception as error:
        raise HTTPException(
            status_code=500, detail="Failed to serve file"
        ) from error


_VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def _resolve_audio_stream_sync(video_id: str) -> tuple[str, bool, dict[str, str]]:
    """Resolve one direct audio URL and the request headers it requires."""
    from musicload.yt_dlp_wrapper import extract_info_with_retry

    youtube_url = f"https://music.youtube.com/watch?v={video_id}"
    ydl_opts = {"format": "bestaudio/best", "quiet": True, "no_warnings": True}
    info = extract_info_with_retry(
        ydl_opts=ydl_opts,
        url=youtube_url,
        download=False,
        cookie_file=config.cookie_file_path,
        config=config,
    )
    selected_format = info
    if "url" in info:
        stream_url = info["url"]
    else:
        audio_formats = [
            item
            for item in info.get("formats", [])
            if item.get("acodec") != "none" and item.get("url")
        ]
        if not audio_formats:
            raise ValueError("No audio stream found")
        selected_format = max(audio_formats, key=lambda item: item.get("abr") or 0)
        stream_url = selected_format["url"]
    protocol = selected_format.get("protocol") or info.get("protocol")
    is_hls = protocol == "m3u8_native" or ".m3u8" in stream_url
    raw_headers = selected_format.get("http_headers") or info.get("http_headers") or {}
    http_headers = {
        str(name): str(value)
        for name, value in raw_headers.items()
        if value is not None and "\r" not in str(name) and "\n" not in str(name)
        and "\r" not in str(value) and "\n" not in str(value)
    }
    return stream_url, is_hls, http_headers


async def _resolve_audio_stream(video_id: str) -> tuple[str, bool, dict[str, str]]:
    if not _VIDEO_ID_RE.fullmatch(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")
    cached = _stream_url_cache.get(video_id)
    if cached is not None:
        return cached
    try:
        resolved = await asyncio.to_thread(_resolve_audio_stream_sync, video_id)
    except Exception as error:
        raise HTTPException(status_code=502, detail="Failed to resolve audio stream") from error
    _stream_url_cache.put(video_id, resolved)
    return resolved


@app.get("/api/stream-url/{video_id}", response_model=StreamUrlResponse)
async def get_stream_url(video_id: str):
    """Get a cached direct stream URL without blocking the event loop."""
    stream_url, is_hls, _ = await _resolve_audio_stream(video_id)
    return StreamUrlResponse(
        video_id=video_id,
        url=stream_url,
        expires_in=300,
        is_hls=is_hls,
    )


@app.get("/api/preview/{video_id}")
async def preview_audio(video_id: str):
    """Stream audio through Musicload instead of exposing the source URL.

    Uses yt-dlp to resolve the stream URL and required headers, then ffmpeg
    converts it to MP3 that browsers can play progressively.
    """
    stream_url, _, http_headers = await _resolve_audio_stream(video_id)

    cmd = _preview_ffmpeg_command(stream_url, http_headers)

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


def _preview_ffmpeg_command(stream_url: str, http_headers: dict[str, str]) -> list[str]:
    """Build the ffmpeg preview command, preserving yt-dlp request headers."""
    cmd = ['ffmpeg', '-nostdin']
    if http_headers:
        ffmpeg_headers = "".join(
            f"{name}: {value}\r\n" for name, value in http_headers.items()
        )
        cmd.extend(['-headers', ffmpeg_headers])
    cmd.extend([
        '-i', stream_url,
        '-vn',
        '-f', 'mp3',
        '-ab', '128k',
        '-loglevel', 'error',
        'pipe:1',
    ])
    return cmd


# Queue endpoints


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

    from musicload.library_index import find_existing_track

    runtime_config = get_config()
    remote_user = _get_remote_user(http_request, runtime_config)
    playlist_name = runtime_config.effective_playlist_name(remote_user)

    existing = await asyncio.to_thread(
        find_existing_track,
        runtime_config.data_dir,
        request.video_id,
        request.title,
        request.artist,
    )
    if existing:
        return QueueAddResponse(status="existing")

    active_job = await queue_manager.find_active_job(request.video_id, audio_format)
    if active_job:
        return QueueAddResponse(job_id=active_job.id, status="active")

    job_id = await queue_manager.add_job(
        video_id=request.video_id,
        title=request.title,
        artist=request.artist,
        format=audio_format,
        artists=request.artists,
        album=request.album,
        playlist_name=playlist_name,
    )

    return QueueAddResponse(job_id=job_id, status="queued")


@app.post("/api/queue/add-album")
async def add_album_to_queue(request: QueueAddAlbumRequest, http_request: Request):
    """Add all tracks from an album to the download queue."""
    if not queue_manager:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")

    import logging

    from musicload.search import get_album_tracks

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

        from musicload.library_index import find_existing_video

        runtime_config = get_config()
        remote_user = _get_remote_user(http_request, runtime_config)
        playlist_name = runtime_config.effective_playlist_name(remote_user)

        job_ids = []
        for track_number, track in enumerate(tracks, start=1):
            existing = await asyncio.to_thread(
                find_existing_video, runtime_config.data_dir, track.video_id
            )
            if existing:
                continue
            active_job = await queue_manager.find_active_job(track.video_id, audio_format)
            if active_job:
                continue
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
                playlist_name=playlist_name,
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
async def remove_queue_job(job_id: str, request: Request, delete_file: bool = False):
    """Remove or clear a job from the queue."""
    if not queue_manager:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")

    job = await queue_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status.value in {"completed", "failed"}:
        _require_admin(request)
    if delete_file:
        if job.status.value != "completed" or not job.file_path:
            raise HTTPException(status_code=400, detail="This job has no completed file to delete")
        _delete_downloaded_audio_file(job.file_path)

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
        previous_payload: str | None = None
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                # Get current jobs
                jobs = await queue_manager.list_jobs()
                jobs_data = [job.to_dict() for job in jobs]
                payload = json.dumps(jobs_data, separators=(",", ":"))

                # Send only state changes. Idle clients need no duplicate payloads.
                if payload != previous_payload:
                    yield f"data: {payload}\n\n"
                    previous_payload = payload

                active = any(
                    job.status.value in {"queued", "downloading"} for job in jobs
                )
                await asyncio.sleep(0.5 if active else 5)

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
app.include_router(explore_router)


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


@app.get("/api/settings")
async def api_get_settings(request: Request):
    """Return effective application settings for an administrator."""
    _require_admin(request)
    return _settings_response()


@app.put("/api/settings")
async def api_save_settings(payload: AppSettingsRequest, request: Request):
    """Persist application settings managed by the web interface."""
    _require_admin(request)
    from musicload.settings import load_settings, save_settings

    effective = get_config()
    existing_overrides = load_settings(effective.data_dir)
    values = build_settings_values(payload, effective, existing_overrides)
    await asyncio.to_thread(save_settings, effective.data_dir, values)
    logger.info("Application settings updated by %s", _current_account_name(request))
    return {
        "success": True,
        "message": "Settings saved. Restart Musicload after changing an option marked restart.",
        **_settings_response(),
    }


@app.delete("/api/settings")
async def api_reset_settings(request: Request):
    """Remove web overrides and return to environment/default configuration."""
    _require_admin(request)
    from musicload.settings import clear_settings

    effective = get_config()
    removed = await asyncio.to_thread(clear_settings, effective.data_dir)
    return {
        "success": True,
        "removed": removed,
        "message": "Web settings reset. Restart Musicload to apply environment defaults.",
    }


# Cookie management endpoints
@app.post("/api/settings/cookies/upload")
async def upload_cookies(request: Request, file: UploadFile = File(...)):
    """Upload cookies.txt file for yt-dlp authentication."""
    _require_admin(request)
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
async def get_cookie_status(request: Request):
    """Check if cookies are configured."""
    _require_admin(request)
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
async def delete_cookies(request: Request):
    """Delete uploaded cookie file."""
    _require_admin(request)
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
    _require_admin(http_request)
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


def _normalise_duplicate_value(value: str | None) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", (value or "").casefold())
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"\W+", " ", text, flags=re.UNICODE).strip()


_DUPLICATE_TITLE_NOISE = {
    "audio",
    "copy",
    "hd",
    "hq",
    "kopie",
    "lyric",
    "lyrics",
    "music",
    "official",
    "original",
    "remaster",
    "remastered",
    "uhd",
    "version",
    "video",
}


def _duplicate_title_words(title: str | None) -> tuple[str, ...]:
    """Return the meaningful words used to compare two song names."""
    normalised_words = _normalise_duplicate_value(title).split()
    words = []
    for word in normalised_words:
        if word in _DUPLICATE_TITLE_NOISE:
            continue
        if len(word) == 4 and word.isdecimal() and 1900 <= int(word) <= 2099:
            continue
        words.append(word)
    return tuple(sorted(words or normalised_words))


def _find_library_duplicates_sync(download_dir: Path) -> dict:
    """Find possible duplicates with the same meaningful song-name words."""
    records: list[dict] = []
    for file_path in _scan_library_files(download_dir):
        entry_path = str(file_path.relative_to(download_dir))
        stat = file_path.stat()
        info = _extract_track_info(entry_path, download_dir)
        records.append(
            {
                "entry_path": entry_path,
                "title": info["title"] or file_path.stem,
                "artist": info["artist"],
                "album": info["album"],
                "duration": info["duration"],
                "file_size": stat.st_size,
                "format": file_path.suffix.lower().lstrip("."),
                "modified_at": stat.st_mtime,
            }
        )

    by_title_words: dict[tuple[str, ...], list[dict]] = {}
    for record in records:
        title_words = _duplicate_title_words(record["title"])
        if not title_words:
            continue
        by_title_words.setdefault(title_words, []).append(record)

    groups = [
        {
            "kind": "name",
            "label": "Matching song name",
            "confidence": "possible",
            "key": " ".join(title_words),
            "matched_words": list(title_words),
            "tracks": matches,
        }
        for title_words, matches in by_title_words.items()
        if len(matches) >= 2
    ]
    groups.sort(key=lambda group: group["key"])
    duplicate_paths = {
        track["entry_path"]
        for group in groups
        for track in group["tracks"]
    }

    return {
        "groups": groups,
        "total_groups": len(groups),
        "duplicate_files": len(duplicate_paths),
        "scanned_files": len(records),
    }


def _build_library_cache_sync() -> dict[str, tuple[float, int, dict]]:
    """Scan Local Files once and reuse unchanged metadata from SQLite."""
    from musicload.web.library_cache import load_cached_files, replace_cached_files

    existing = load_cached_files(config.data_dir)
    refreshed: dict[str, tuple[float, int, dict]] = {}
    for file_path in _scan_library_files(config.download_dir):
        entry_path = str(file_path.relative_to(config.download_dir))
        stat = file_path.stat()
        cached = existing.get(entry_path)
        if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            metadata = cached[2]
        else:
            metadata = _extract_track_info(entry_path, config.download_dir)
        refreshed[entry_path] = (stat.st_mtime, stat.st_size, metadata)
    replace_cached_files(config.data_dir, refreshed)
    return refreshed


async def _warm_library_cache() -> None:
    """Populate the in-memory Local Files index in the background at startup."""
    global _library_metadata_cache
    try:
        _library_metadata_cache = await asyncio.to_thread(_build_library_cache_sync)
        logger.info("Warmed Local Files cache with %d files", len(_library_metadata_cache))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to warm Local Files cache")


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
    request: Request,
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200),
):
    """List local audio files on disk, most recently added first."""
    _require_admin(request)
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
        stat = f.stat()
        cached = _library_metadata_cache.get(rel_path)
        if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            info = cached[2]
        else:
            info = await loop.run_in_executor(None, _extract_track_info, rel_path, download_dir)
            _library_metadata_cache[rel_path] = (stat.st_mtime, stat.st_size, info)
            from musicload.web.library_cache import set_cached_file
            await asyncio.to_thread(
                set_cached_file,
                config.data_dir,
                rel_path,
                stat.st_mtime,
                stat.st_size,
                info,
            )
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


@app.get("/api/library/duplicates")
async def api_library_duplicates(request: Request):
    """Scan the local library for possible duplicates based on song names."""
    _require_admin(request)
    runtime_config = get_config()
    try:
        return await asyncio.to_thread(
            _find_library_duplicates_sync, runtime_config.download_dir
        )
    except OSError as error:
        logger.error("Duplicate scan failed: %s", error)
        raise HTTPException(status_code=500, detail=f"Duplicate scan failed: {error}")


@app.get("/api/library/play")
async def api_library_play_file(
    request: Request,
    entry_path: str = Query(..., description="Relative path of the audio file"),
):
    """Stream a local audio file for playback in the browser."""
    _require_admin(request)
    config = get_config()
    abs_path = _resolve_library_path(entry_path, config.download_dir)
    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    from musicload.tagging import SUPPORTED_EXTENSIONS
    if abs_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Not an audio file")
    return FileResponse(path=abs_path, filename=abs_path.name)


@app.get("/api/library/thumbnail")
async def api_library_thumbnail(
    request: Request,
    entry_path: str = Query(..., description="Relative path of the audio file"),
):
    """Return embedded artwork or a conventional album-folder cover."""
    _require_admin(request)
    config = get_config()
    abs_path = _resolve_library_path(entry_path, config.download_dir)
    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if artwork := embedded_artwork(abs_path):
        data, media_type = artwork
        return Response(content=data, media_type=media_type)
    if cover_path := folder_artwork(abs_path):
        media_type = mimetypes.guess_type(cover_path.name)[0] or "image/jpeg"
        return FileResponse(cover_path, media_type=media_type)
    raise HTTPException(status_code=404, detail="No embedded cover")


@app.delete("/api/library/files")
async def api_library_delete_file(
    request: Request,
    entry_path: str = Query(..., description="Relative path of the file to delete"),
):
    """Delete a local audio file from disk."""
    _require_admin(request)
    rel_entry = _delete_downloaded_audio_file(entry_path)
    logger.info("Deleted library file: %s", rel_entry)
    return {"success": True, "message": f"Deleted: {rel_entry}"}
