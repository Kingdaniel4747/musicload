"""ReplayGain/R128 loudness normalization tagging via rsgain."""

import logging
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def is_rsgain_available() -> bool:
    """Check if rsgain binary is available on PATH. Result is cached."""
    available = shutil.which("rsgain") is not None
    if not available:
        logger.warning("rsgain not found on PATH; ReplayGain tagging will be skipped")
    return available


def apply_replaygain(audio_path: Path, audio_format: str) -> bool:
    """Apply ReplayGain 2.0 tags to an audio file using rsgain.

    For Opus files, uses RFC 7845 R128 output gain tags (-o r).
    Non-fatal: logs warnings on failure and returns False.

    Args:
        audio_path: Path to the audio file
        audio_format: Audio format (opus, mp3, flac, etc.)

    Returns:
        True if tags were applied successfully, False otherwise
    """
    if not is_rsgain_available():
        return False

    if not audio_path.exists():
        logger.warning("ReplayGain: file not found: %s", audio_path)
        return False

    cmd = ["rsgain", "custom", "-q", "-s", "i"]

    # Use RFC 7845 R128 tags for Opus
    if audio_format == "opus":
        cmd.extend(["-o", "r"])

    cmd.append(str(audio_path))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.warning(
                "ReplayGain failed (exit %d) for %s: %s",
                result.returncode,
                audio_path.name,
                result.stderr.strip(),
            )
            return False

        logger.info("ReplayGain tags applied: %s", audio_path.name)
        return True

    except subprocess.TimeoutExpired:
        logger.warning("ReplayGain timed out for %s", audio_path.name)
        return False
    except FileNotFoundError:
        logger.warning("rsgain binary disappeared; ReplayGain skipped for %s", audio_path.name)
        return False
    except Exception as e:
        logger.warning("ReplayGain error for %s: %s", audio_path.name, e)
        return False
