"""Deezer playlist support."""

import logging
import re

import httpx

from musicload.models.deezer import DeezerTrack

logger = logging.getLogger(__name__)

DEEZER_PLAYLIST_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?deezer\.com/(?:[a-z]{2}/)?playlist/(\d+)"
)
DEEZER_API_BASE = "https://api.deezer.com"
DEEZER_PAGE_SIZE = 100


class DeezerAPIError(ValueError):
    """Generic Deezer API failure."""


class DeezerQuotaError(DeezerAPIError):
    """Raised when Deezer API quota is exceeded."""


def is_deezer_url(url: str) -> bool:
    """Check if a URL is a Deezer playlist URL."""
    return bool(DEEZER_PLAYLIST_PATTERN.match(url))


def _extract_playlist_id(url: str) -> int:
    """Extract Deezer playlist ID from URL."""
    match = DEEZER_PLAYLIST_PATTERN.match(url)
    if not match:
        raise ValueError(f"Invalid Deezer playlist URL: {url}")
    return int(match.group(1))


def _request_deezer_json(path: str, params: dict | None = None) -> dict:
    """Issue a Deezer API request and raise rich errors."""
    response = httpx.get(
        f"{DEEZER_API_BASE}{path}",
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    error = payload.get("error")
    if error:
        message = str(error.get("message", "Unknown error"))
        code = error.get("code")
        if code == 4:
            raise DeezerQuotaError("Deezer API quota limit exceeded. Please retry later.")
        raise DeezerAPIError(f"Deezer API error: {message} (code {code})")

    return payload


def _track_from_payload(track_data: dict) -> DeezerTrack:
    """Convert Deezer track JSON into DeezerTrack."""
    contributors = track_data.get("contributors") or []
    contributor_names = [
        str(artist.get("name", "")).strip()
        for artist in contributors
        if isinstance(artist, dict)
    ]
    artists = [name for name in contributor_names if name]

    main_artist = (track_data.get("artist") or {}).get("name")
    if not artists and isinstance(main_artist, str) and main_artist.strip():
        artists = [main_artist.strip()]
    if not artists:
        artists = ["Unknown Artist"]

    album_title = None
    album = track_data.get("album")
    if isinstance(album, dict):
        value = album.get("title")
        if isinstance(value, str) and value.strip():
            album_title = value.strip()

    duration_seconds = int(track_data.get("duration", 0) or 0)

    return DeezerTrack(
        name=str(track_data.get("title", "Unknown")),
        artist=artists[0],
        artists=artists,
        album=album_title,
        duration_ms=duration_seconds * 1000,
    )


def get_playlist_tracks(url: str) -> list[DeezerTrack]:
    """
    Fetch tracks from a Deezer playlist URL.

    Uses Deezer REST pagination to avoid per-track API calls that quickly hit quota.
    """
    playlist_id = _extract_playlist_id(url)

    # Initial call is used only to read total/metadata.
    playlist_payload = _request_deezer_json(f"/playlist/{playlist_id}")
    playlist_title = playlist_payload.get("title", f"Playlist {playlist_id}")
    logger.info("Fetching Deezer playlist: %s", playlist_title)

    tracks: list[DeezerTrack] = []
    index = 0
    total = int((playlist_payload.get("nb_tracks") or 0) or 0)

    while True:
        tracks_payload = _request_deezer_json(
            f"/playlist/{playlist_id}/tracks",
            params={"index": index, "limit": DEEZER_PAGE_SIZE},
        )
        page = tracks_payload.get("data") or []
        if not page:
            break

        for item in page:
            if isinstance(item, dict):
                tracks.append(_track_from_payload(item))

        index += len(page)
        if total and index >= total:
            break

    logger.info("Found %d tracks in Deezer playlist", len(tracks))
    return tracks


def get_tracks_from_url(url: str) -> list[DeezerTrack]:
    """Fetch tracks from a Deezer URL."""
    if DEEZER_PLAYLIST_PATTERN.match(url):
        return get_playlist_tracks(url)
    raise ValueError(f"Unsupported Deezer URL: {url}")
