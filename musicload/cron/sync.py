"""Playlist synchronization logic."""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yt_dlp

from musicload.config import get_config
from musicload.cron.config import PlaylistConfig
from musicload.cron.state import PlaylistState, TrackState, get_state_dir, load_state, save_state
from musicload.download import download
from musicload.playlist import add_to_m3u, rebuild_m3u
from musicload.reference_checker import is_safe_to_delete
from musicload.unavailable import is_on_cooldown, is_unavailable_error, record_unavailable
from musicload.yt_dlp_wrapper import extract_info_with_retry

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a playlist sync operation."""

    downloaded: int
    skipped: int
    deleted: int
    failed: int


def sync_playlist(
    playlist_config: PlaylistConfig,
    download_dir: Path,
    audio_format: str,
    filename_template: str,
    organization_mode: str = "flat",
    use_primary_artist: bool = False,
) -> SyncResult:
    """
    Synchronize a playlist with its configured source.

    Args:
        playlist_config: Playlist configuration
        download_dir: Download directory
        audio_format: Audio format (opus, mp3, flac)
        filename_template: Filename template for downloads
        organization_mode: File organization mode ("flat" or "album")
        use_primary_artist: Extract primary artist for folder (before feat., &, etc.)

    Returns:
        SyncResult with counts of operations performed
    """
    logger.info("Starting sync for playlist: %s", playlist_config.name)

    config = get_config()
    state_dir = get_state_dir(config.data_dir)

    try:
        # Fetch current tracks from URL
        current_tracks = fetch_current_tracks(playlist_config.url)
        if not current_tracks:
            logger.warning("No tracks found in playlist: %s", playlist_config.name)
            return SyncResult(downloaded=0, skipped=0, deleted=0, failed=0)

        logger.info("Found %d track(s) in playlist", len(current_tracks))

        # Load existing state
        state = load_state(state_dir, playlist_config.name)
        if not state:
            # Create fresh state
            state = PlaylistState(
                playlist_name=playlist_config.name,
                url=playlist_config.url,
                last_check=datetime.now().isoformat(),
                tracks=[],
            )
            logger.info("Created fresh state for playlist: %s", playlist_config.name)

        # Compare tracks
        new_tracks, removed_tracks = compare_tracks(current_tracks, state)

        logger.info(
            "Changes detected: %d new, %d removed",
            len(new_tracks),
            len(removed_tracks),
        )

        # Download new tracks
        download_result = download_new_tracks(
            new_tracks,
            download_dir,
            audio_format,
            filename_template,
            state,
            organization_mode,
            use_primary_artist,
        )

        # Remove old tracks if sync=true
        deleted_count = 0
        if playlist_config.sync and removed_tracks:
            deleted_count = remove_old_tracks(removed_tracks, state, download_dir, config.data_dir)

        # Update M3U playlist
        update_m3u_playlist(playlist_config.name, state, download_dir)

        # Update and save state
        state.url = playlist_config.url
        state.last_check = datetime.now().isoformat()
        save_state(state_dir, state)

        result = SyncResult(
            downloaded=download_result["downloaded"],
            skipped=download_result["skipped"],
            deleted=deleted_count,
            failed=download_result["failed"],
        )

        logger.info(
            "Sync completed for %s: %d downloaded, %d skipped, %d deleted, %d failed",
            playlist_config.name,
            result.downloaded,
            result.skipped,
            result.deleted,
            result.failed,
        )

        return result

    except Exception as e:
        logger.error("Sync failed for %s: %s", playlist_config.name, e)
        return SyncResult(downloaded=0, skipped=0, deleted=0, failed=1)


def fetch_current_tracks(url: str) -> list[tuple[str, str, str]]:
    """
    Fetch current tracks from a playlist URL.

    Handles YouTube/YouTube Music and Deezer URLs.

    Args:
        url: Playlist URL

    Returns:
        List of tuples: (video_id, title, artist)
    """
    # Check if Deezer URL
    from musicload.deezer import is_deezer_url

    if is_deezer_url(url):
        return _fetch_deezer_tracks(url)
    else:
        return _fetch_youtube_tracks(url)


def _fetch_youtube_tracks(url: str) -> list[tuple[str, str, str]]:
    """Fetch tracks from YouTube/YouTube Music playlist."""
    config = get_config()

    try:
        ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
        info = extract_info_with_retry(
            ydl_opts=ydl_opts,
            url=url,
            download=False,
            cookie_file=config.cookie_file_path,
            config=config,
        )

        # Check if this is a playlist
        if info.get("_type") != "playlist" and "entries" not in info:
            # Single video, treat as one-item playlist
            video_id = info.get("id")
            title = info.get("title", "Unknown")
            artist = info.get("artist") or info.get("uploader", "Unknown")
            return [(video_id, title, artist)]

        # Extract tracks from playlist
        entries = info.get("entries", [])
        tracks = []
        for entry in entries:
            if not entry:
                continue
            video_id = entry.get("id")
            title = entry.get("title", "Unknown")
            artist = entry.get("artist") or entry.get("uploader", "Unknown")
            if video_id:
                tracks.append((video_id, title, artist))

        return tracks

    except Exception as e:
        logger.error("Failed to fetch YouTube playlist: %s", e)
        return []


def _fetch_deezer_tracks(url: str) -> list[tuple[str, str, str]]:
    """
    Fetch tracks from Deezer playlist and search YouTube Music.

    Returns video IDs from YouTube Music search results.
    """
    try:
        from musicload.deezer import get_tracks_from_url
        from musicload.search import search

        deezer_tracks = get_tracks_from_url(url)
        if not deezer_tracks:
            return []

        youtube_tracks = []
        for dz_track in deezer_tracks:
            # Search YouTube Music for this track
            results = search(dz_track.search_query, limit=1)
            if results:
                yt_track = results[0]
                youtube_tracks.append((yt_track.video_id, yt_track.title, yt_track.artist))
            else:
                logger.warning(
                    "Could not find on YouTube Music: %s - %s",
                    dz_track.artist,
                    dz_track.name,
                )

        return youtube_tracks

    except Exception as e:
        logger.error("Failed to fetch Deezer playlist: %s", e)
        return []


def compare_tracks(
    current: list[tuple[str, str, str]], state: PlaylistState
) -> tuple[list[tuple[str, str, str]], list[TrackState]]:
    """
    Compare current tracks with state to identify changes.

    Args:
        current: Current tracks from playlist
        state: Existing playlist state

    Returns:
        Tuple of (new_tracks, removed_tracks)
    """
    current_ids = {track[0] for track in current}
    state_ids = {track.video_id for track in state.tracks}

    new_ids = current_ids - state_ids
    removed_ids = state_ids - current_ids

    new_tracks = [t for t in current if t[0] in new_ids]
    removed_tracks = [t for t in state.tracks if t.video_id in removed_ids]

    return new_tracks, removed_tracks


def download_new_tracks(
    tracks: list[tuple],
    download_dir: Path,
    audio_format: str,
    filename_template: str,
    state: PlaylistState,
    organization_mode: str = "flat",
    use_primary_artist: bool = False,
) -> dict:
    """
    Download new tracks and update state.

    Args:
        tracks: List of tuples (video_id, title, artist), optionally followed
            by an album metadata dict for tracks obtained from an album source.
        download_dir: Download directory
        audio_format: Audio format
        filename_template: Filename template
        state: Playlist state to update
        organization_mode: File organization mode ("flat" or "album")
        use_primary_artist: Extract primary artist for folder (before feat., &, etc.)

    Returns:
        Dict with counts: downloaded, skipped, failed
    """
    downloaded = 0
    skipped = 0
    failed = 0

    from musicload.config import get_config
    config = get_config()
    cooldown_hours = config.unavailable_cooldown_hours

    for i, track in enumerate(tracks, 1):
        video_id, title, artist = track[:3]
        album_metadata = track[3] if len(track) > 3 else {}
        # Check if video is on unavailable cooldown
        if is_on_cooldown(config.data_dir, video_id, cooldown_hours):
            logger.info(
                "[%d/%d] Skipping (unavailable cooldown): %s - %s",
                i, len(tracks), artist, title,
            )
            skipped += 1
            continue

        logger.info("[%d/%d] Downloading: %s - %s", i, len(tracks), artist, title)

        try:
            audio_path = download(
                video_id=video_id,
                output_dir=download_dir,
                audio_format=audio_format,
                filename_template=filename_template,
                fetch_lyrics=True,
                organization_mode=organization_mode,
                use_primary_artist=use_primary_artist,
                cookie_file=config.cookie_file_path,
                apply_replaygain=config.replaygain,
                album=album_metadata.get("album"),
                album_artist=album_metadata.get("album_artist"),
                album_year=album_metadata.get("album_year"),
                track_number=album_metadata.get("track_number"),
            )

            if audio_path:
                # Add to state
                track_state = TrackState(
                    video_id=video_id,
                    title=title,
                    artist=artist,
                    file_path=str(audio_path),
                    downloaded_at=datetime.now().isoformat(),
                )
                state.tracks.append(track_state)
                downloaded += 1
            else:
                # download() returned None (file not found after download)
                failed += 1

        except Exception as e:
            logger.error("Failed to download %s - %s: %s", artist, title, e)
            failed += 1
            # Record unavailable videos for cooldown
            if is_unavailable_error(str(e)):
                record_unavailable(config.data_dir, video_id, str(e), title=title, artist=artist)

    return {"downloaded": downloaded, "skipped": skipped, "failed": failed}


def remove_old_tracks(
    tracks: list[TrackState],
    state: PlaylistState,
    download_dir: Path,
    data_dir: Path | None = None,
) -> int:
    """
    Remove tracks that are no longer in the playlist.

    Only deletes files if they are not referenced by other playlists or plugins.
    Deletes audio files, .lrc files, and removes from state.

    Args:
        tracks: Tracks to remove
        state: Playlist state to update
        download_dir: Download directory (used to check cross-references)
        data_dir: Data directory for state files (defaults to config.data_dir)

    Returns:
        Number of tracks deleted
    """
    if data_dir is None:
        from musicload.config import get_config
        data_dir = get_config().data_dir
    deleted_count = 0
    skipped_count = 0

    for track in tracks:
        logger.info("Removing: %s - %s", track.artist, track.title)

        file_path = Path(track.file_path)

        # Check if file is referenced by other playlists/plugins
        if file_path.exists():
            if not is_safe_to_delete(
                file_path,
                data_dir,
                current_playlist_name=state.playlist_name,
            ):
                logger.info(
                    "Skipping deletion of %s (referenced by other playlists/plugins)",
                    file_path.name,
                )
                skipped_count += 1
                # Still remove from current playlist state
                state.tracks = [t for t in state.tracks if t.video_id != track.video_id]
                continue

            # Safe to delete - no other references
            try:
                file_path.unlink()
                logger.debug("Deleted: %s", file_path)
                deleted_count += 1
            except Exception as e:
                logger.error("Failed to delete %s: %s", file_path, e)
                continue

            # Delete .lrc file if exists
            lrc_path = file_path.with_suffix(".lrc")
            try:
                lrc_path.unlink()
                logger.debug("Deleted lyrics: %s", lrc_path)
            except FileNotFoundError:
                pass  # Already deleted, that's fine
            except Exception as e:
                logger.warning("Failed to delete lyrics %s: %s", lrc_path, e)

        # Remove from state
        state.tracks = [t for t in state.tracks if t.video_id != track.video_id]

    if skipped_count > 0:
        logger.info(
            "Skipped deletion of %d file(s) referenced by other sources",
            skipped_count,
        )

    return deleted_count


def update_m3u_playlist(
    playlist_name: str, state: PlaylistState, download_dir: Path
) -> None:
    """
    Update M3U playlist file with current tracks.

    Rebuilds the playlist from current state to maintain order.

    Args:
        playlist_name: Playlist name
        state: Current playlist state
        download_dir: Download directory
    """
    if not state.tracks:
        logger.debug("No tracks in state, skipping M3U update")
        return

    # Get all file paths that actually exist
    file_paths = []
    for track in state.tracks:
        path = Path(track.file_path)
        if path.exists():
            file_paths.append(path)
        else:
            logger.warning("Track file not found: %s", path)

    try:
        rebuild_m3u(file_paths, playlist_name, download_dir)
        logger.debug("Updated M3U playlist: %s.m3u", playlist_name)
    except Exception as e:
        logger.error("Failed to update M3U playlist: %s", e)
