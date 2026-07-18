"""Plugin synchronization logic."""

import logging
from datetime import datetime
from pathlib import Path

from musicload.download import download
from musicload.playlist import rebuild_m3u
from musicload.plugins.base import Plugin, PluginConfig, Song, SyncResult
from musicload.plugins.state import (
    PluginState,
    PluginTrackState,
    get_state_dir,
    load_plugin_state,
    save_plugin_state,
)
from musicload.reference_checker import is_safe_to_delete
from musicload.search import search
from musicload.unavailable import is_on_cooldown, is_unavailable_error, record_unavailable

logger = logging.getLogger(__name__)


def sync_plugin_instance(
    plugin: Plugin,
    config: PluginConfig,
    sync_mode: bool,
) -> SyncResult:
    """Sync a plugin instance.

    Args:
        plugin: Plugin instance
        config: Plugin configuration
        sync_mode: If True, delete removed tracks

    Returns:
        SyncResult with operation counts
    """
    from musicload.config import get_config as get_app_config
    app_config = get_app_config()

    logger.info("Starting plugin sync: %s (%s)", config.name, plugin.name)

    state_dir = get_state_dir(app_config.data_dir)

    try:
        # Fetch songs from plugin
        current_songs = plugin.fetch_songs(config)
        logger.info("Plugin returned %d songs", len(current_songs))

        if not current_songs:
            logger.warning("No songs returned by plugin")
            return SyncResult(
                plugin_name=config.name,
                downloaded=0,
                skipped=0,
                failed=0,
                errors=[],
            )

        # Load existing state
        state = load_plugin_state(state_dir, config.name)
        if not state:
            state = PluginState(
                plugin_name=config.name,
                plugin_type=plugin.name,
                source_url=config.config.get("url"),
                last_check=datetime.now().isoformat(),
                tracks=[],
            )

        # Compare songs
        new_songs, removed_tracks = _compare_songs(current_songs, state)
        logger.info("Changes: %d new, %d removed", len(new_songs), len(removed_tracks))

        # Download new songs
        download_result = _download_songs(
            new_songs,
            config,
            state,
        )

        # Remove old tracks if sync=true
        deleted_count = 0
        if sync_mode and removed_tracks:
            deleted_count = _remove_tracks(removed_tracks, state, config.download_dir, app_config.data_dir)

        # Update M3U playlist
        _update_m3u(config.name, state, config.download_dir)

        # Update and save state
        state.last_check = datetime.now().isoformat()
        if "url" in config.config:
            state.source_url = config.config["url"]
        save_plugin_state(state_dir, state)

        result = SyncResult(
            plugin_name=config.name,
            downloaded=download_result["downloaded"],
            skipped=download_result["skipped"],
            failed=download_result["failed"],
            errors=download_result["errors"],
        )

        logger.info(
            "Plugin sync completed: %d downloaded, %d skipped, %d deleted, %d failed",
            result.downloaded,
            result.skipped,
            deleted_count,
            result.failed,
        )

        return result

    except Exception as e:
        logger.error("Plugin sync failed: %s", e)
        return SyncResult(
            plugin_name=config.name,
            downloaded=0,
            skipped=0,
            failed=1,
            errors=[str(e)],
        )


def _compare_songs(
    current: list[Song],
    state: PluginState,
) -> tuple[list[Song], list[PluginTrackState]]:
    """Compare current songs with state."""
    current_keys = {song.cache_key for song in current}
    state_keys = {track.cache_key for track in state.tracks}

    new_keys = current_keys - state_keys
    removed_keys = state_keys - current_keys

    new_songs = [s for s in current if s.cache_key in new_keys]
    removed_tracks = [t for t in state.tracks if t.cache_key in removed_keys]

    return new_songs, removed_tracks


def _download_songs(
    songs: list[Song],
    config: PluginConfig,
    state: PluginState,
) -> dict:
    """Download songs and update state."""
    downloaded = 0
    skipped = 0
    failed = 0
    errors = []

    from musicload.config import get_config
    app_config = get_config()
    cooldown_hours = app_config.unavailable_cooldown_hours

    for i, song in enumerate(songs, 1):
        logger.info("[%d/%d] Searching: %s - %s", i, len(songs), song.artist, song.title)

        # Check if already in state (by cache_key)
        if any(t.cache_key == song.cache_key for t in state.tracks):
            logger.info("  Already downloaded, skipping")
            skipped += 1
            continue

        video_id = None
        try:
            # Search YouTube Music
            results = search(song.search_query, limit=1)

            if not results:
                logger.warning("  Not found on YouTube Music")
                failed += 1
                errors.append(f"Not found: {song.artist} - {song.title}")
                continue

            yt_track = results[0]
            video_id = yt_track.video_id

            # Check if found video is on unavailable cooldown
            if is_on_cooldown(app_config.data_dir, video_id, cooldown_hours):
                logger.info(
                    "  Skipping (unavailable cooldown): %s - %s",
                    yt_track.artist, yt_track.title,
                )
                skipped += 1
                continue

            logger.info("  Found: %s - %s", yt_track.artist, yt_track.title)

            # Download
            audio_path = download(
                video_id=video_id,
                output_dir=config.download_dir,
                audio_format=config.audio_format,
                filename_template=config.filename_template,
                fetch_lyrics=True,
                organization_mode=config.organization_mode,
                use_primary_artist=config.use_primary_artist,
                apply_replaygain=app_config.replaygain,
            )

            if audio_path:
                # Add to state
                track_state = PluginTrackState(
                    cache_key=song.cache_key,
                    artist=song.artist,
                    title=song.title,
                    file_path=str(audio_path),
                    downloaded_at=datetime.now().isoformat(),
                    video_id=video_id,
                )
                state.tracks.append(track_state)
                downloaded += 1

        except Exception as e:
            logger.error("  Failed: %s", e)
            failed += 1
            errors.append(f"{song.artist} - {song.title}: {e}")
            # Record unavailable videos for cooldown
            if video_id and is_unavailable_error(str(e)):
                record_unavailable(
                    app_config.data_dir, video_id, str(e),
                    title=song.title, artist=song.artist,
                )

    return {
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }


def _remove_tracks(
    tracks: list[PluginTrackState],
    state: PluginState,
    download_dir: Path,
    data_dir: Path | None = None,
) -> int:
    """Remove tracks from filesystem and state.

    Only deletes files if they are not referenced by other playlists or plugins.

    Args:
        tracks: Tracks to remove
        state: Plugin state to update
        download_dir: Download directory (used to check cross-references)
        data_dir: Data directory for state files (defaults to config.data_dir)

    Returns:
        Number of tracks deleted
    """
    if data_dir is None:
        from musicload.config import get_config
        data_dir = get_config().data_dir
    deleted = 0
    skipped = 0

    for track in tracks:
        logger.info("Removing: %s - %s", track.artist, track.title)

        file_path = Path(track.file_path)

        # Check if file is referenced by other playlists/plugins
        if file_path.exists():
            if not is_safe_to_delete(
                file_path,
                data_dir,
                current_plugin_name=state.plugin_name,
            ):
                logger.info(
                    "Skipping deletion of %s (referenced by other playlists/plugins)",
                    file_path.name,
                )
                skipped += 1
                # Still remove from current plugin state
                state.tracks = [t for t in state.tracks if t.cache_key != track.cache_key]
                continue

            # Safe to delete - no other references
            try:
                file_path.unlink()
                deleted += 1

                # Remove .lrc if exists
                lrc_path = file_path.with_suffix(".lrc")
                if lrc_path.exists():
                    lrc_path.unlink()
            except Exception as e:
                logger.error("Failed to delete %s: %s", file_path, e)
                continue

        # Remove from state
        state.tracks = [t for t in state.tracks if t.cache_key != track.cache_key]

    if skipped > 0:
        logger.info(
            "Skipped deletion of %d file(s) referenced by other sources",
            skipped,
        )

    return deleted


def _update_m3u(name: str, state: PluginState, download_dir: Path) -> None:
    """Update M3U playlist."""
    if not state.tracks:
        return

    file_paths = []
    for track in state.tracks:
        path = Path(track.file_path)
        if path.exists():
            file_paths.append(path)

    try:
        rebuild_m3u(file_paths, name, download_dir)
    except Exception as e:
        logger.error("Failed to update M3U: %s", e)
