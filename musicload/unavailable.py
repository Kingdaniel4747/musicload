"""Unavailable video cooldown management.

Tracks YouTube video IDs that returned "Video unavailable" errors and
prevents retrying them until a configurable cooldown period has elapsed.

Storage: {data_dir}/unavailable.json
Format: {"video_id": {"failed_at": "ISO timestamp", "error": "error message", "title": "optional title"}}
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from musicload.models.unavailable import UnavailableRecord

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_HOURS = 168  # 7 days

# Patterns indicating the video itself is unavailable (not auth/network issues).
# Keep these specific: bare "This video is not available" is ambiguous and may be
# returned for transient extractor/client issues in some environments (e.g. Docker).
UNAVAILABLE_PATTERNS = [
    r"Video unavailable",
    r"This video is not available in your country",
    r"This video has been removed",
    r"This video is no longer available",
    r"This video does not exist",
    r"The uploader has not made this video available",
]


def is_unavailable_error(error_message: str) -> bool:
    """Check if an error message indicates a video is unavailable.

    Only matches errors where the video itself is unavailable, not
    auth errors, network issues, or other transient failures.

    Args:
        error_message: Error message string from yt-dlp

    Returns:
        True if the error indicates the video is unavailable
    """
    for pattern in UNAVAILABLE_PATTERNS:
        if re.search(pattern, error_message, re.IGNORECASE):
            return True
    return False


def get_unavailable_file(data_dir: Path) -> Path:
    """Get the path to the unavailable videos JSON file.

    Creates the parent directory if it doesn't exist.

    Args:
        data_dir: Data directory (e.g. ~/.musicload)

    Returns:
        Path to unavailable.json
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "unavailable.json"


def load_unavailable(data_dir: Path) -> dict:
    """Load the unavailable videos registry from disk.

    Args:
        data_dir: Data directory (e.g. ~/.musicload)

    Returns:
        Dict mapping video_id to failure record
    """
    unavailable_file = get_unavailable_file(data_dir)

    if not unavailable_file.exists():
        return {}

    try:
        content = unavailable_file.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            logger.warning("Unavailable file has unexpected format, resetting")
            return {}
        return data
    except json.JSONDecodeError as e:
        logger.error("Corrupted unavailable file: %s", e)
        # Backup corrupted file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = unavailable_file.with_suffix(f".json.corrupt.{timestamp}")
        unavailable_file.rename(backup)
        logger.info("Backed up corrupted unavailable file to: %s", backup)
        return {}
    except Exception as e:
        logger.error("Failed to load unavailable file: %s", e)
        return {}


def save_unavailable(data_dir: Path, data: dict) -> None:
    """Save the unavailable videos registry to disk using atomic write.

    Args:
        data_dir: Data directory (e.g. ~/.musicload)
        data: Dict mapping video_id to failure record
    """
    unavailable_file = get_unavailable_file(data_dir)
    temp_file = unavailable_file.with_suffix(".json.tmp")

    try:
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        temp_file.write_text(json_str, encoding="utf-8")
        temp_file.replace(unavailable_file)
        logger.debug("Saved unavailable registry (%d entries)", len(data))
    except Exception as e:
        logger.error("Failed to save unavailable file: %s", e)
        if temp_file.exists():
            temp_file.unlink()
        raise


def record_unavailable(
    data_dir: Path,
    video_id: str,
    error_message: str,
    title: str | None = None,
    artist: str | None = None,
) -> None:
    """Record a video as unavailable.

    Args:
        data_dir: Data directory (e.g. ~/.musicload)
        video_id: YouTube video ID
        error_message: The error message from yt-dlp
        title: Optional track title for logging
        artist: Optional track artist for logging
    """
    data = load_unavailable(data_dir)

    record = UnavailableRecord(
        failed_at=datetime.now(timezone.utc).isoformat(),
        error=error_message,
        title=title,
        artist=artist,
    )
    data[video_id] = record.model_dump()

    save_unavailable(data_dir, data)

    display_name = f"{artist} - {title}" if artist and title else video_id
    logger.info("Recorded unavailable video: %s (cooldown active)", display_name)


def is_on_cooldown(
    data_dir: Path,
    video_id: str,
    cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
) -> bool:
    """Check if a video ID is on cooldown due to being unavailable.

    Args:
        data_dir: Data directory (e.g. ~/.musicload)
        video_id: YouTube video ID to check
        cooldown_hours: Hours to wait before retrying (0 = disabled)

    Returns:
        True if the video is still on cooldown
    """
    if cooldown_hours <= 0:
        return False

    data = load_unavailable(data_dir)

    if video_id not in data:
        return False

    record = data[video_id]
    failed_at_str = record.get("failed_at")

    if not failed_at_str:
        return False

    try:
        failed_at = datetime.fromisoformat(failed_at_str)
        # Ensure timezone-aware comparison
        if failed_at.tzinfo is None:
            failed_at = failed_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        cooldown_end = failed_at + timedelta(hours=cooldown_hours)
        return now < cooldown_end
    except (ValueError, TypeError) as e:
        logger.warning("Invalid timestamp for video %s: %s", video_id, e)
        return False


def get_cooldown_remaining(
    data_dir: Path,
    video_id: str,
    cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
) -> timedelta | None:
    """Get remaining cooldown time for a video.

    Args:
        data_dir: Data directory (e.g. ~/.musicload)
        video_id: YouTube video ID
        cooldown_hours: Hours to wait before retrying

    Returns:
        Remaining cooldown timedelta, or None if not on cooldown
    """
    if cooldown_hours <= 0:
        return None

    data = load_unavailable(data_dir)

    if video_id not in data:
        return None

    record = data[video_id]
    failed_at_str = record.get("failed_at")

    if not failed_at_str:
        return None

    try:
        failed_at = datetime.fromisoformat(failed_at_str)
        if failed_at.tzinfo is None:
            failed_at = failed_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        cooldown_end = failed_at + timedelta(hours=cooldown_hours)
        remaining = cooldown_end - now
        if remaining.total_seconds() > 0:
            return remaining
        return None
    except (ValueError, TypeError):
        return None


def clear_expired(
    data_dir: Path,
    cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
) -> int:
    """Remove expired entries from the unavailable registry.

    Args:
        data_dir: Data directory (e.g. ~/.musicload)
        cooldown_hours: Hours after which entries expire

    Returns:
        Number of entries removed
    """
    data = load_unavailable(data_dir)

    if not data:
        return 0

    now = datetime.now(timezone.utc)
    expired_ids = []

    for video_id, record in data.items():
        failed_at_str = record.get("failed_at")
        if not failed_at_str:
            expired_ids.append(video_id)
            continue

        try:
            failed_at = datetime.fromisoformat(failed_at_str)
            if failed_at.tzinfo is None:
                failed_at = failed_at.replace(tzinfo=timezone.utc)
            if now >= failed_at + timedelta(hours=cooldown_hours):
                expired_ids.append(video_id)
        except (ValueError, TypeError):
            expired_ids.append(video_id)

    if expired_ids:
        for video_id in expired_ids:
            del data[video_id]
        save_unavailable(data_dir, data)
        logger.info("Cleared %d expired unavailable entries", len(expired_ids))

    return len(expired_ids)
