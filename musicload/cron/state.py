"""Playlist state management."""

import json
import logging
from datetime import datetime
from pathlib import Path

from musicload.models.state import PlaylistState, TrackState

logger = logging.getLogger(__name__)


def get_state_dir(data_dir: Path) -> Path:
    """
    Get the state directory path.

    Creates directory if it doesn't exist.

    Args:
        data_dir: Data directory (e.g. ~/.musicload)

    Returns:
        Path to state directory ({data_dir}/state)
    """
    state_dir = data_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def load_state(state_dir: Path, playlist_name: str) -> PlaylistState | None:
    """
    Load playlist state from JSON file.

    Args:
        state_dir: State directory path
        playlist_name: Playlist name

    Returns:
        PlaylistState if file exists and is valid, None otherwise
    """
    state_file = state_dir / f"{playlist_name}.json"

    if not state_file.exists():
        logger.debug("State file not found: %s", state_file)
        return None

    try:
        content = state_file.read_text(encoding="utf-8")
        data = json.loads(content)
        return PlaylistState.model_validate(data)

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.error("Corrupted state file: %s - %s", state_file, e)
        # Backup corrupted file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = state_dir / f"{playlist_name}.json.corrupt.{timestamp}"
        state_file.rename(backup_file)
        logger.info("Backed up corrupted state to: %s", backup_file)
        return None

    except Exception as e:
        logger.error("Failed to load state file %s: %s", state_file, e)
        return None


def save_state(state_dir: Path, state: PlaylistState) -> None:
    """
    Save playlist state to JSON file using atomic write.

    Args:
        state_dir: State directory path
        state: Playlist state to save
    """
    state_file = state_dir / f"{state.playlist_name}.json"
    temp_file = state_dir / f"{state.playlist_name}.json.tmp"

    try:
        # Serialize to JSON
        data = state.model_dump()
        json_str = json.dumps(data, indent=2, ensure_ascii=False)

        # Write to temp file
        temp_file.write_text(json_str, encoding="utf-8")

        # Atomic replace (cross-platform, overwrites existing file)
        temp_file.replace(state_file)

        logger.debug("Saved state for playlist: %s", state.playlist_name)

    except Exception as e:
        logger.error("Failed to save state for %s: %s", state.playlist_name, e)
        # Clean up temp file if it exists
        if temp_file.exists():
            temp_file.unlink()
        raise
