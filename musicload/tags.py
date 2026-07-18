"""Multi-valued tag support for audio files using mutagen.

This module handles writing ARTISTS and ALBUMARTISTS tags as multi-valued
entries for Navidrome compatibility. Different audio formats require
different approaches:

- FLAC/Opus/Ogg: Use Vorbis Comments (native multi-value support)
- MP3: Use TXXX frames (user-defined text) for true multi-value tags
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def write_multi_artist_tags(
    file_path: Path,
    artists: list[str],
    album_artists: list[str] | None = None,
) -> bool:
    """
    Write multi-valued ARTISTS and ALBUMARTISTS tags to an audio file.

    Preserves existing ARTIST tag for backward compatibility.
    Handles FLAC, MP3, and Opus formats differently.

    Args:
        file_path: Path to audio file
        artists: List of individual artist names
        album_artists: List of album artist names (optional, defaults to artists)

    Returns:
        True if successful, False on error
    """
    if not artists:
        logger.debug("No artists provided, skipping tag writing")
        return True

    if not file_path.exists():
        logger.warning("File does not exist: %s", file_path)
        return False

    suffix = file_path.suffix.lower()

    try:
        if suffix == ".flac":
            return _write_flac_tags(file_path, artists, album_artists)
        elif suffix == ".opus":
            return _write_opus_tags(file_path, artists, album_artists)
        elif suffix == ".ogg":
            return _write_ogg_tags(file_path, artists, album_artists)
        elif suffix == ".mp3":
            return _write_mp3_tags(file_path, artists, album_artists)
        else:
            logger.debug("Unsupported format for multi-artist tags: %s", suffix)
            return True  # Not an error, just unsupported
    except Exception as e:
        logger.warning("Failed to write multi-artist tags to %s: %s", file_path.name, e)
        return False


def _write_flac_tags(
    file_path: Path,
    artists: list[str],
    album_artists: list[str] | None,
) -> bool:
    """Write multi-valued tags to FLAC file using Vorbis Comments."""
    from mutagen.flac import FLAC

    audio = FLAC(file_path)

    # Write multi-valued ARTISTS tag
    audio["ARTISTS"] = artists

    # Write ALBUMARTISTS (use artists if not specified)
    audio["ALBUMARTISTS"] = album_artists if album_artists else artists

    audio.save()
    logger.debug("Wrote ARTISTS tag to FLAC: %s (%d artists)", file_path.name, len(artists))
    return True


def _write_opus_tags(
    file_path: Path,
    artists: list[str],
    album_artists: list[str] | None,
) -> bool:
    """Write multi-valued tags to Opus file using Vorbis Comments."""
    from mutagen.oggopus import OggOpus

    audio = OggOpus(file_path)

    # Write multi-valued ARTISTS tag
    audio["ARTISTS"] = artists

    # Write ALBUMARTISTS (use artists if not specified)
    audio["ALBUMARTISTS"] = album_artists if album_artists else artists

    audio.save()
    logger.debug("Wrote ARTISTS tag to Opus: %s (%d artists)", file_path.name, len(artists))
    return True


def _write_ogg_tags(
    file_path: Path,
    artists: list[str],
    album_artists: list[str] | None,
) -> bool:
    """Write multi-valued tags to Ogg Vorbis file."""
    from mutagen.oggvorbis import OggVorbis

    audio = OggVorbis(file_path)

    # Write multi-valued ARTISTS tag
    audio["ARTISTS"] = artists

    # Write ALBUMARTISTS (use artists if not specified)
    audio["ALBUMARTISTS"] = album_artists if album_artists else artists

    audio.save()
    logger.debug("Wrote ARTISTS tag to Ogg: %s (%d artists)", file_path.name, len(artists))
    return True


def _write_mp3_tags(
    file_path: Path,
    artists: list[str],
    album_artists: list[str] | None,
) -> bool:
    """Write multi-valued tags to MP3 file using TXXX frames."""
    from mutagen.id3 import ID3, TXXX, ID3NoHeaderError

    try:
        audio = ID3(file_path)
    except ID3NoHeaderError:
        # Create ID3 tag if not present
        audio = ID3()

    # Remove existing TXXX:ARTISTS and TXXX:ALBUMARTISTS if present
    audio.delall("TXXX:ARTISTS")
    audio.delall("TXXX:ALBUMARTISTS")

    # Add ARTISTS as TXXX frame with multiple values
    # Using encoding=3 for UTF-8
    audio.add(TXXX(encoding=3, desc="ARTISTS", text=artists))

    # Add ALBUMARTISTS
    album_artist_list = album_artists if album_artists else artists
    audio.add(TXXX(encoding=3, desc="ALBUMARTISTS", text=album_artist_list))

    audio.save(file_path)
    logger.debug("Wrote ARTISTS tag to MP3: %s (%d artists)", file_path.name, len(artists))
    return True
