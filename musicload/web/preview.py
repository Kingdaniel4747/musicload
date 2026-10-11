"""Browser-compatible YouTube previews, isolated from library downloads."""

import asyncio
import logging
import re
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from musicload.config import get_config
from musicload.web.api_cache import TtlCache
from musicload.youtube_options import youtube_ydl_options

router = APIRouter()
logger = logging.getLogger(__name__)
_stream_candidate_cache = TtlCache(max_entries=50, ttl_seconds=300)

class StreamUrlResponse(BaseModel):
    """Response for stream URL endpoint."""

    video_id: str
    url: str
    expires_in: int
    is_hls: bool = False


_VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")
_UPSTREAM_URL_RE = re.compile(r"https?://\S+")
_PREVIEW_FIRST_BYTE_TIMEOUT = 20
_PREVIEW_CANDIDATE_LIMIT = 8


@dataclass(frozen=True)
class _AudioStreamCandidate:
    """One yt-dlp format candidate that ffmpeg can try server-side."""

    url: str
    http_headers: dict[str, str]
    format_id: str
    protocol: str
    is_hls: bool


def _format_quality(item: dict) -> float:
    for key in ("abr", "tbr"):
        try:
            value = float(item.get(key) or 0)
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    return 0


def _candidate_priority(item: dict) -> tuple[int, float]:
    """Prefer token-free HLS and avoid downloading video when possible."""
    url = str(item.get("url") or "")
    protocol = str(item.get("protocol") or "").lower()
    is_hls = "m3u8" in protocol or ".m3u8" in url.lower()
    audio_only = item.get("vcodec") == "none"
    if is_hls and audio_only:
        group = 4
    elif is_hls:
        group = 3
    elif audio_only:
        group = 2
    else:
        group = 1
    return group, _format_quality(item)


def _safe_http_headers(item: dict, fallback: dict) -> dict[str, str]:
    raw_headers = item.get("http_headers") or fallback or {}
    return {
        str(name): str(value)
        for name, value in raw_headers.items()
        if value is not None
        and "\r" not in str(name)
        and "\n" not in str(name)
        and "\r" not in str(value)
        and "\n" not in str(value)
    }


def _resolve_audio_candidates_sync(video_id: str) -> list[_AudioStreamCandidate]:
    """Resolve multiple candidates so a rejected Google URL is not fatal."""
    from musicload.yt_dlp_wrapper import extract_info_with_retry

    config = get_config()
    youtube_url = f"https://music.youtube.com/watch?v={video_id}"
    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    ydl_opts.update(youtube_ydl_options())
    info = extract_info_with_retry(
        ydl_opts=ydl_opts,
        url=youtube_url,
        download=False,
        cookie_file=config.cookie_file_path,
        config=config,
    )

    raw_formats: list[dict] = []
    for key in ("requested_formats", "requested_downloads", "formats"):
        value = info.get(key)
        if isinstance(value, list):
            raw_formats.extend(item for item in value if isinstance(item, dict))
    if info.get("url"):
        raw_formats.append(info)

    fallback_headers = info.get("http_headers") or {}
    candidates: list[_AudioStreamCandidate] = []
    seen_urls: set[str] = set()
    for item in sorted(raw_formats, key=_candidate_priority, reverse=True):
        stream_url = str(item.get("url") or "")
        if not stream_url or stream_url in seen_urls:
            continue
        if item.get("has_drm") or item.get("acodec") == "none":
            continue
        protocol = str(item.get("protocol") or info.get("protocol") or "unknown")
        candidates.append(
            _AudioStreamCandidate(
                url=stream_url,
                http_headers=_safe_http_headers(item, fallback_headers),
                format_id=str(item.get("format_id") or "unknown")[:64],
                protocol=protocol[:64],
                is_hls="m3u8" in protocol.lower() or ".m3u8" in stream_url.lower(),
            )
        )
        seen_urls.add(stream_url)
        if len(candidates) >= _PREVIEW_CANDIDATE_LIMIT:
            break

    if not candidates:
        raise ValueError("No usable audio stream candidates found")
    return candidates


async def _resolve_audio_candidates(video_id: str) -> list[_AudioStreamCandidate]:
    if not _VIDEO_ID_RE.fullmatch(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")
    cached = _stream_candidate_cache.get(video_id)
    if cached is not None:
        return cached
    try:
        resolved = await asyncio.to_thread(_resolve_audio_candidates_sync, video_id)
    except Exception as error:
        logger.warning(
            "Failed to resolve preview candidates video_id=%s error=%s",
            video_id,
            type(error).__name__,
        )
        raise HTTPException(status_code=502, detail="Failed to resolve audio stream") from error
    _stream_candidate_cache.put(video_id, resolved)
    return resolved


def _preview_error_summary(stderr: bytes) -> str:
    message = stderr.decode(errors="replace").strip()
    if not message:
        return "ffmpeg returned no audio and no error message"
    message = _UPSTREAM_URL_RE.sub("<upstream-url>", message)
    return " ".join(message.split())[:800]


async def _stop_preview_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass  # The child may have exited between the check and kill.
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        logger.warning("Timed out while stopping ffmpeg preview process")


async def _start_preview_candidate(
    candidate: _AudioStreamCandidate,
) -> tuple[asyncio.subprocess.Process, bytes, asyncio.Task[bytes]] | None:
    process = await asyncio.create_subprocess_exec(
        *_preview_ffmpeg_command(candidate.url, candidate.http_headers),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_task = asyncio.create_task(process.stderr.read())
    try:
        first_chunk = await asyncio.wait_for(
            process.stdout.read(16_384),
            timeout=_PREVIEW_FIRST_BYTE_TIMEOUT,
        )
    except TimeoutError:
        await _stop_preview_process(process)
        stderr = await stderr_task
        logger.warning(
            "Preview candidate timed out format=%s protocol=%s error=%s",
            candidate.format_id,
            candidate.protocol,
            _preview_error_summary(stderr),
        )
        return None
    except asyncio.CancelledError:
        # Cancelling a browser request during startup must not orphan ffmpeg.
        await _stop_preview_process(process)
        await stderr_task
        raise

    if first_chunk:
        return process, first_chunk, stderr_task

    await _stop_preview_process(process)
    stderr = await stderr_task
    logger.warning(
        "Preview candidate failed format=%s protocol=%s exit_code=%s error=%s",
        candidate.format_id,
        candidate.protocol,
        process.returncode,
        _preview_error_summary(stderr),
    )
    return None


@router.get("/api/stream-url/{video_id}", response_model=StreamUrlResponse)
async def get_stream_url(video_id: str):
    """Keep API compatibility without exposing short-lived Google URLs."""
    if not _VIDEO_ID_RE.fullmatch(video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")
    return StreamUrlResponse(
        video_id=video_id,
        url=f"/api/preview/{video_id}",
        expires_in=0,
        is_hls=False,
    )


@router.get("/api/preview/{video_id}")
async def preview_audio(video_id: str):
    try:
        async with asyncio.timeout(55):
            return await _prepare_preview(video_id)
    except TimeoutError as error:
        _stream_candidate_cache.discard(video_id)
        raise HTTPException(
            status_code=504, detail="Playback preparation timed out; please try again"
        ) from error


async def _prepare_preview(video_id: str):
    """Stream audio through Musicload instead of exposing the source URL.

    Uses yt-dlp to resolve the stream URL and required headers, then ffmpeg
    converts it to MP3 that browsers can play progressively.
    """
    candidates = await _resolve_audio_candidates(video_id)
    for candidate in candidates:
        started = await _start_preview_candidate(candidate)
        if started is None:
            continue
        process, first_chunk, stderr_task = started
        logger.info(
            "Preview started video_id=%s format=%s protocol=%s hls=%s",
            video_id,
            candidate.format_id,
            candidate.protocol,
            candidate.is_hls,
        )

        async def stream_audio():
            reached_eof = False
            try:
                yield first_chunk
                while True:
                    chunk = await process.stdout.read(16_384)
                    if not chunk:
                        reached_eof = True
                        break
                    yield chunk
            finally:
                await _stop_preview_process(process)
                stderr = await stderr_task
                if reached_eof and process.returncode not in (0, None):
                    logger.warning(
                        "Preview stream ended with error video_id=%s format=%s "
                        "exit_code=%s error=%s",
                        video_id,
                        candidate.format_id,
                        process.returncode,
                        _preview_error_summary(stderr),
                    )

        return StreamingResponse(
            stream_audio(),
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    _stream_candidate_cache.discard(video_id)
    logger.error("All preview candidates failed video_id=%s", video_id)
    raise HTTPException(
        status_code=502,
        detail="YouTube rejected every available audio stream; please try again",
    )


def _preview_ffmpeg_command(stream_url: str, http_headers: dict[str, str]) -> list[str]:
    """Build the ffmpeg preview command, preserving yt-dlp request headers."""
    cmd = [
        'ffmpeg',
        '-nostdin',
        '-hide_banner',
        '-loglevel', 'error',
        '-reconnect', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '3',
    ]
    if http_headers:
        ffmpeg_headers = "".join(
            f"{name}: {value}\r\n" for name, value in http_headers.items()
        )
        cmd.extend(['-headers', ffmpeg_headers])
    cmd.extend([
        '-i', stream_url,
        '-map', '0:a:0',
        '-vn',
        '-c:a', 'libmp3lame',
        '-f', 'mp3',
        '-b:a', '128k',
        'pipe:1',
    ])
    return cmd


