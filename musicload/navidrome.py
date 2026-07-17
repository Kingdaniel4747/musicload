"""Navidrome/Subsonic API client for protecting starred songs and playlists."""

import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


@dataclass
class NavidromeSong:
    """Song metadata from Navidrome/Subsonic API."""

    id: str
    path: str
    title: str
    artist: str
    album: str | None = None


class NavidromeClient:
    """Client for Navidrome/Subsonic API."""

    def __init__(self, url: str, username: str, password: str):
        """
        Initialize client with authentication.

        Args:
            url: Navidrome base URL (e.g., https://music.example.com)
            username: Navidrome username
            password: Navidrome password
        """
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.client = httpx.Client(timeout=30.0)

    def _get_auth_params(self) -> dict[str, str]:
        """
        Generate Subsonic API authentication parameters.

        Uses token-based authentication (MD5 hash) per Subsonic API spec.

        Returns:
            Dict of auth parameters to include in API requests
        """
        salt = secrets.token_hex(8)
        token = hashlib.md5(f"{self.password}{salt}".encode()).hexdigest()

        return {
            "u": self.username,
            "t": token,
            "s": salt,
            "v": "1.16.1",
            "c": "musicload",
            "f": "json",
        }

    def _make_request(self, endpoint: str, params: dict | None = None) -> dict:
        """
        Make authenticated API request with error handling.

        Args:
            endpoint: API endpoint (e.g., "getStarred2")
            params: Additional query parameters

        Returns:
            Response JSON data

        Raises:
            httpx.HTTPError: On network errors
            ValueError: On API errors or invalid response
        """
        url = f"{self.url}/rest/{endpoint}"

        # Combine auth params with additional params
        request_params = self._get_auth_params()
        if params:
            request_params.update(params)

        try:
            response = self.client.get(url, params=request_params)
            response.raise_for_status()

            data = response.json()

            # Check Subsonic API response status
            subsonic_response = data.get("subsonic-response", {})
            status = subsonic_response.get("status")

            if status != "ok":
                error = subsonic_response.get("error", {})
                error_message = error.get("message", "Unknown error")
                error_code = error.get("code", "N/A")
                raise ValueError(
                    f"Subsonic API error {error_code}: {error_message}"
                )

            return subsonic_response

        except httpx.HTTPStatusError as e:
            logger.error(
                "HTTP error %s from Navidrome: %s",
                e.response.status_code,
                e.response.text,
            )
            raise
        except httpx.RequestError as e:
            logger.error("Network error connecting to Navidrome: %s", e)
            raise
        except (ValueError, KeyError) as e:
            logger.error("Invalid response from Navidrome: %s", e)
            raise

    def get_starred_songs(self) -> list[NavidromeSong]:
        """
        Fetch all starred songs via getStarred2 endpoint.

        Returns:
            List of starred songs, empty list on error
        """
        try:
            response = self._make_request("getStarred2")
            starred = response.get("starred2", {})
            songs_data = starred.get("song", [])

            songs = []
            for song_data in songs_data:
                song = NavidromeSong(
                    id=song_data.get("id", ""),
                    path=song_data.get("path", ""),
                    title=song_data.get("title", "Unknown"),
                    artist=song_data.get("artist", "Unknown"),
                    album=song_data.get("album"),
                )
                songs.append(song)

            logger.debug("Fetched %d starred songs from Navidrome", len(songs))
            return songs

        except Exception as e:
            logger.warning("Failed to fetch starred songs: %s", e)
            return []

    def get_playlists(self) -> list[dict]:
        """
        Fetch all playlists via getPlaylists endpoint.

        Returns:
            List of playlist dicts with 'id' and 'name' keys, empty list on error
        """
        try:
            response = self._make_request("getPlaylists")
            playlists_data = response.get("playlists", {})
            playlists = playlists_data.get("playlist", [])

            logger.debug("Fetched %d playlists from Navidrome", len(playlists))
            return playlists

        except Exception as e:
            logger.warning("Failed to fetch playlists: %s", e)
            return []

    def get_playlist_songs(self, playlist_id: str) -> list[NavidromeSong]:
        """
        Fetch songs from specific playlist via getPlaylist endpoint.

        Args:
            playlist_id: Navidrome playlist ID

        Returns:
            List of songs in playlist, empty list on error
        """
        try:
            response = self._make_request("getPlaylist", params={"id": playlist_id})
            playlist = response.get("playlist", {})
            songs_data = playlist.get("entry", [])

            songs = []
            for song_data in songs_data:
                song = NavidromeSong(
                    id=song_data.get("id", ""),
                    path=song_data.get("path", ""),
                    title=song_data.get("title", "Unknown"),
                    artist=song_data.get("artist", "Unknown"),
                    album=song_data.get("album"),
                )
                songs.append(song)

            logger.debug(
                "Fetched %d songs from playlist %s",
                len(songs),
                playlist_id,
            )
            return songs

        except Exception as e:
            logger.warning("Failed to fetch playlist %s: %s", playlist_id, e)
            return []

    def find_playlist_by_name(self, name: str) -> str | None:
        """
        Find playlist ID by name (case-insensitive).

        Args:
            name: Playlist name to search for

        Returns:
            Playlist ID if found, None otherwise
        """
        playlists = self.get_playlists()
        name_lower = name.lower()

        for playlist in playlists:
            if playlist.get("name", "").lower() == name_lower:
                return playlist.get("id")

        return None


def is_navidrome_enabled() -> bool:
    """
    Check if Navidrome integration is configured via env vars.

    Returns:
        True if NAVIDROME_URL, NAVIDROME_USER, and NAVIDROME_PASSWORD are all set
    """
    url = os.getenv("NAVIDROME_URL")
    user = os.getenv("NAVIDROME_USER")
    password = os.getenv("NAVIDROME_PASSWORD")

    return bool(url and user and password)


def get_navidrome_client() -> NavidromeClient | None:
    """
    Get configured Navidrome client or None if disabled.

    Returns:
        NavidromeClient instance if configured, None otherwise
    """
    if not is_navidrome_enabled():
        return None

    url = os.getenv("NAVIDROME_URL", "")
    user = os.getenv("NAVIDROME_USER", "")
    password = os.getenv("NAVIDROME_PASSWORD", "")

    return NavidromeClient(url, user, password)


def normalize_string(s: str) -> str:
    """
    Normalize string for fuzzy matching.

    Converts to lowercase and removes special characters.

    Args:
        s: String to normalize

    Returns:
        Normalized string
    """
    # Convert to lowercase
    s = s.lower()

    # Remove common special characters and extra whitespace
    s = s.replace("&", "and")
    for char in [",", ".", "!", "?", "'", '"', "(", ")", "[", "]", "{", "}"]:
        s = s.replace(char, "")

    # Normalize whitespace
    s = " ".join(s.split())

    return s


def match_by_path(local_path: Path, navidrome_song: NavidromeSong) -> bool:
    """
    Match by filename comparison (basename without extension).

    Args:
        local_path: Path to local audio file
        navidrome_song: Navidrome song to compare against

    Returns:
        True if filenames match (ignoring extension)
    """
    # Get basename without extension
    local_basename = local_path.stem
    navidrome_basename = Path(navidrome_song.path).stem

    return local_basename == navidrome_basename


def match_by_metadata(
    artist: str, title: str, navidrome_song: NavidromeSong
) -> bool:
    """
    Match by normalized artist+title.

    Args:
        artist: Local file artist
        title: Local file title
        navidrome_song: Navidrome song to compare against

    Returns:
        True if normalized artist+title match
    """
    # Normalize both sides
    local_key = normalize_string(f"{artist} {title}")
    navidrome_key = normalize_string(
        f"{navidrome_song.artist} {navidrome_song.title}"
    )

    return local_key == navidrome_key


def is_song_in_navidrome_list(
    file_path: Path,
    artist: str,
    title: str,
    navidrome_songs: list[NavidromeSong],
    reason: str,
) -> tuple[bool, str]:
    """
    Check if song matches any in Navidrome list.

    Args:
        file_path: Path to local audio file
        artist: Local file artist
        title: Local file title
        navidrome_songs: List of Navidrome songs to check against
        reason: Reason string (e.g., "starred", "in playlist 'keep'")

    Returns:
        Tuple of (matched: bool, reason: str)
    """
    for song in navidrome_songs:
        # Try path matching first (fast and accurate)
        if match_by_path(file_path, song):
            return True, reason

        # Fallback to metadata matching
        if match_by_metadata(artist, title, song):
            return True, reason

    return False, ""
