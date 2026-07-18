"""Reference checking for safe file deletion across playlists and plugins."""

import logging
from pathlib import Path

from musicload.cron.state import load_state as load_playlist_state
from musicload.plugins.state import load_plugin_state

logger = logging.getLogger(__name__)


def is_safe_to_delete(
    file_path: Path,
    data_dir: Path,
    current_playlist_name: str | None = None,
    current_plugin_name: str | None = None,
) -> bool:
    """
    Check if a file can be safely deleted without breaking other playlists/plugins.

    This function scans all playlist and plugin state files to determine if
    the given file is referenced by any other source besides the current one.

    Args:
        file_path: Path to the file to check
        data_dir: Data directory (e.g. ~/.musicload) containing state files
        current_playlist_name: Name of the playlist requesting deletion (to exclude from check)
        current_plugin_name: Name of the plugin requesting deletion (to exclude from check)

    Returns:
        True if safe to delete (no other references), False otherwise
    """
    file_path_str = str(file_path)

    # Check playlist states
    playlist_state_dir = data_dir / "state"
    if playlist_state_dir.exists():
        for state_file in playlist_state_dir.glob("*.json"):
            # Skip temp files and current playlist
            if state_file.suffix == ".tmp":
                continue

            playlist_name = state_file.stem
            if playlist_name == current_playlist_name:
                continue

            # Load state and check for file reference
            try:
                state = load_playlist_state(playlist_state_dir, playlist_name)
                if state:
                    for track in state.tracks:
                        if track.file_path == file_path_str:
                            logger.info(
                                "File %s is still referenced by playlist '%s', not deleting",
                                file_path.name,
                                playlist_name,
                            )
                            return False
            except Exception as e:
                logger.warning(
                    "Failed to load playlist state %s: %s",
                    state_file,
                    e,
                )
                # Continue checking other states

    # Check plugin states
    plugin_state_dir = data_dir / "plugin_state"
    if plugin_state_dir.exists():
        for state_file in plugin_state_dir.glob("*.json"):
            # Skip temp files and current plugin
            if state_file.suffix == ".tmp":
                continue

            plugin_name = state_file.stem
            if plugin_name == current_plugin_name:
                continue

            # Load state and check for file reference
            try:
                state = load_plugin_state(plugin_state_dir, plugin_name)
                if state:
                    for track in state.tracks:
                        if track.file_path == file_path_str:
                            logger.info(
                                "File %s is still referenced by plugin '%s', not deleting",
                                file_path.name,
                                plugin_name,
                            )
                            return False
            except Exception as e:
                logger.warning(
                    "Failed to load plugin state %s: %s",
                    state_file,
                    e,
                )
                # Continue checking other states

    # No other references found, safe to delete
    return True
