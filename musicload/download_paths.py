"""Filesystem naming and duplicate-path helpers for downloads."""

from pathlib import Path

import yt_dlp

from musicload.config import MAX_FILENAME_BYTES

_AUDIO_EXTENSIONS = ("opus", "mp3", "m4a", "flac", "webm")


def _sanitize_path_component(name: str, max_bytes: int = MAX_FILENAME_BYTES) -> str:
    """Sanitize a string for use as a directory or file name component.

    Removes invalid filesystem characters, replaces slashes, strips whitespace,
    and truncates to max_bytes to prevent "File name too long" errors.
    """
    # Remove characters that are invalid in filenames/paths
    invalid_chars = '<>:"|?*'
    for char in invalid_chars:
        name = name.replace(char, "")
    # Replace forward/backslash with dash
    name = name.replace("/", "-").replace("\\", "-")
    # Strip leading/trailing whitespace and dots
    name = name.strip(". ")
    # Truncate to max_bytes to prevent filesystem errors
    name = _truncate_to_bytes(name, max_bytes)
    return name or "Unknown"


def _truncate_to_bytes(text: str, max_bytes: int) -> str:
    """Truncate a string to fit within max_bytes when UTF-8 encoded.

    Avoids splitting multi-byte characters by encoding character-by-character.
    Strips trailing whitespace after truncation.
    """
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    encoded = b""
    for char in text:
        char_bytes = char.encode("utf-8")
        if len(encoded) + len(char_bytes) > max_bytes:
            break
        encoded += char_bytes
    return encoded.decode("utf-8").rstrip()


def _get_primary_artist(artist: str) -> str:
    """Extract primary artist from multi-artist string.

    Splits on common separators and returns the first artist.

    Examples:
        "Queen, David Bowie" -> "Queen"
        "Artist feat. Guest" -> "Artist"
        "Artist & Other" -> "Artist"
        "Artist" -> "Artist"

    Args:
        artist: Full artist string (may contain multiple artists)

    Returns:
        Primary artist name
    """
    # Common separators for multi-artist strings (in priority order)
    separators = [
        " feat. ",
        " ft. ",
        " featuring ",
        " with ",
        " & ",
        ", ",
    ]

    # Try each separator and return first part if found
    for separator in separators:
        if separator in artist:
            return artist.split(separator)[0].strip()

    # No separator found, return as-is
    return artist.strip()


def _get_output_path(
    output_dir: Path,
    info: dict,
    filename_template: str,
    organization_mode: str,
    use_primary_artist: bool = False,
) -> str:
    """
    Calculate output path based on organization mode.

    Args:
        output_dir: Base download directory
        info: yt-dlp metadata dict
        filename_template: Filename template (used in flat mode)
        organization_mode: "flat" or "album"
        use_primary_artist: Extract primary artist for folder (before feat., &, etc.)

    Returns:
        Full output path template for yt-dlp
    """
    if organization_mode == "flat":
        # Current behavior: flat structure
        return str(output_dir / f"{filename_template}.%(ext)s")

    # Album mode: organize by artist/album
    artist = info.get("artist") or info.get("uploader", "Unknown Artist")

    # Extract primary artist if requested
    if use_primary_artist:
        artist = _get_primary_artist(artist)

    artist = _sanitize_path_component(artist)

    album = info.get("album")
    year = info.get("release_year")
    track_number = info.get("track_number")

    # Build path components
    path_parts = [str(output_dir), artist]

    if album:
        # We have album info
        album = _sanitize_path_component(album)
        if year:
            album_folder = f"{year} - {album}"
        else:
            album_folder = album
        path_parts.append(album_folder)

        # Build filename with optional track number
        if track_number:
            filename = f"{track_number:02d} - %(title)s.%(ext)s"
        else:
            filename = "%(title)s.%(ext)s"
    else:
        # No album info: just Artist/Track.ext
        filename = "%(title)s.%(ext)s"

    return str(Path(*path_parts) / filename)


def _compute_filename(info: dict, filename_template: str) -> str:
    """Compute the expected filename from metadata using yt-dlp's template.

    Uses trim_file_name to match the truncation applied during download.
    """
    with yt_dlp.YoutubeDL(
        {"outtmpl": filename_template, "trim_file_name": MAX_FILENAME_BYTES}
    ) as ydl:
        filename = ydl.prepare_filename(info)
    return yt_dlp.utils.sanitize_filename(filename)


def _file_exists(
    output_dir: Path,
    info: dict,
    audio_format: str,
    filename_template: str,
    organization_mode: str,
    use_primary_artist: bool = False,
) -> Path | None:
    """Check if a file with the expected name already exists."""
    if organization_mode == "flat":
        # Existing flat mode logic
        expected_name = _compute_filename(info, filename_template)

        for ext in [audio_format, "opus", "mp3", "m4a", "flac"]:
            # Check exact match
            exact_path = output_dir / f"{expected_name}.{ext}"
            if exact_path.exists():
                return exact_path

            # Check with glob for partial matches (handles long titles)
            matches = list(output_dir.glob(f"{expected_name[:50]}*.{ext}"))
            if matches:
                return matches[0]

        return None

    # Album mode: search in artist/album subdirectories
    artist = info.get("artist") or info.get("uploader", "Unknown Artist")
    if use_primary_artist:
        artist = _get_primary_artist(artist)
    artist = _sanitize_path_component(artist)
    artist_dir = output_dir / artist

    if not artist_dir.exists():
        return None

    # Search for the file recursively in artist directory
    title = info.get("title", "Unknown")
    for ext in [audio_format, "opus", "mp3", "m4a", "flac"]:
        # Try with and without track number
        for file_path in artist_dir.rglob(f"*{title}*.{ext}"):
            if file_path.is_file():
                return file_path

    return None


def _newest_audio_file(directory: Path, recursive: bool) -> Path | None:
    finder = directory.rglob if recursive else directory.glob
    files = [
        path for extension in _AUDIO_EXTENSIONS for path in finder(f"*.{extension}")
    ]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def _find_downloaded_file(
    output_dir: Path,
    info: dict,
    audio_format: str,
    filename_template: str,
    organization_mode: str,
    use_primary_artist: bool = False,
) -> Path | None:
    """Find the downloaded audio file in the output directory."""
    if organization_mode == "flat":
        expected_name = _compute_filename(info, filename_template)
        for extension in (audio_format, "opus", "m4a", "webm"):
            exact_path = output_dir / f"{expected_name}.{extension}"
            if exact_path.exists():
                return exact_path
            matches = list(output_dir.glob(f"{expected_name[:50]}*.{extension}"))
            if matches:
                return matches[0]
        return _newest_audio_file(output_dir, recursive=False)

    artist = info.get("artist") or info.get("uploader", "Unknown Artist")
    if use_primary_artist:
        artist = _get_primary_artist(artist)
    artist_dir = output_dir / _sanitize_path_component(artist)
    if not artist_dir.exists():
        return None
    return _newest_audio_file(artist_dir, recursive=True)
