"""M3U playlist management."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def add_to_m3u(
    file_paths: list[Path],
    playlist_name: str,
    download_dir: Path,
) -> Path:
    """
    Add audio file paths to an M3U playlist.

    Creates playlist if it doesn't exist. Skips duplicate entries.
    Uses relative paths for portability.

    Args:
        file_paths: List of audio file paths to add
        playlist_name: Playlist name (without .m3u extension)
        download_dir: Directory where playlist file is stored

    Returns:
        Path to the playlist file
    """
    m3u_path = _get_m3u_path(playlist_name, download_dir)

    # Read existing entries
    existing_entries = set()
    if m3u_path.exists():
        content = m3u_path.read_text(encoding="utf-8").strip()
        if content:
            existing_entries = set(content.split("\n"))
            existing_entries.discard("")  # Remove empty lines

    # Convert to relative paths and add new entries
    new_entries = []
    added_count = 0

    for file_path in file_paths:
        if not file_path.exists():
            logger.warning("File not found, skipping: %s", file_path)
            continue

        rel_path = _make_relative_path(file_path, m3u_path.parent)

        if rel_path not in existing_entries:
            new_entries.append(rel_path)
            existing_entries.add(rel_path)
            added_count += 1

    # Write updated playlist atomically
    if new_entries or not m3u_path.exists():
        # Preserve original order, append new entries
        if m3u_path.exists():
            original_content = m3u_path.read_text(encoding="utf-8").strip()
            original_entries = [line for line in original_content.split("\n") if line and line not in new_entries]
            all_entries = original_entries + new_entries
        else:
            all_entries = new_entries

        # Write to temp file then rename (atomic on POSIX)
        temp_path = m3u_path.with_suffix(".m3u.tmp")
        temp_path.write_text("\n".join(all_entries) + "\n", encoding="utf-8")
        temp_path.rename(m3u_path)

        if added_count > 0:
            logger.info("Added %d track(s) to playlist: %s", added_count, m3u_path.name)
        elif not m3u_path.exists():
            logger.info("Created playlist: %s", m3u_path.name)

    return m3u_path


def _get_m3u_path(playlist_name: str, download_dir: Path) -> Path:
    """Get the full path to an M3U playlist file."""
    # Remove .m3u extension if user provided it
    name = playlist_name.removesuffix(".m3u")
    return download_dir / f"{name}.m3u"


def read_m3u(playlist_name: str, download_dir: Path) -> list[str]:
    """
    Read entries from an M3U playlist file.

    Args:
        playlist_name: Playlist name (without .m3u extension)
        download_dir: Directory where playlist file is stored

    Returns:
        List of entry strings (relative paths). Empty list if file doesn't exist.
    """
    m3u_path = _get_m3u_path(playlist_name, download_dir)

    if not m3u_path.exists():
        return []

    content = m3u_path.read_text(encoding="utf-8").strip()
    if not content:
        return []

    return [line for line in content.split("\n") if line]


def remove_from_m3u(entry_path: str, playlist_name: str, download_dir: Path) -> bool:
    """
    Remove an exact entry from an M3U playlist.

    Writes atomically via temp file + replace.

    Args:
        entry_path: Exact entry string to remove (relative path as stored in M3U)
        playlist_name: Playlist name (without .m3u extension)
        download_dir: Directory where playlist file is stored

    Returns:
        True if entry was found and removed, False otherwise.
    """
    m3u_path = _get_m3u_path(playlist_name, download_dir)

    if not m3u_path.exists():
        return False

    content = m3u_path.read_text(encoding="utf-8").strip()
    if not content:
        return False

    entries = [line for line in content.split("\n") if line]
    if entry_path not in entries:
        return False

    entries = [e for e in entries if e != entry_path]

    # Write atomically
    temp_path = m3u_path.with_suffix(".m3u.tmp")
    if entries:
        temp_path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    else:
        temp_path.write_text("", encoding="utf-8")
    temp_path.replace(m3u_path)

    logger.info("Removed entry from playlist %s: %s", m3u_path.name, entry_path)
    return True


def rebuild_m3u(
    file_paths: list[Path],
    playlist_name: str,
    download_dir: Path,
) -> Path:
    """
    Rewrite an M3U playlist to contain exactly the given entries.

    Unlike add_to_m3u() which only appends, this function replaces the
    playlist contents entirely. Used by sync operations to remove stale
    entries for deleted tracks.

    Args:
        file_paths: Authoritative list of audio file paths
        playlist_name: Playlist name (without .m3u extension)
        download_dir: Directory where playlist file is stored

    Returns:
        Path to the playlist file
    """
    m3u_path = _get_m3u_path(playlist_name, download_dir)

    entries = []
    for file_path in file_paths:
        if not file_path.exists():
            logger.warning("File not found, skipping: %s", file_path)
            continue
        entries.append(_make_relative_path(file_path, m3u_path.parent))

    # Write atomically
    temp_path = m3u_path.with_suffix(".m3u.tmp")
    if entries:
        temp_path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    else:
        temp_path.write_text("", encoding="utf-8")
    temp_path.replace(m3u_path)

    logger.debug("Rebuilt M3U playlist: %s (%d tracks)", m3u_path.name, len(entries))
    return m3u_path


def _make_relative_path(file_path: Path, relative_to: Path) -> str:
    """
    Convert absolute path to relative path.

    Args:
        file_path: Absolute path to audio file
        relative_to: Directory to make path relative to (playlist location)

    Returns:
        Relative path as string
    """
    try:
        return str(file_path.relative_to(relative_to))
    except ValueError:
        # Files not in same tree, use absolute path
        logger.warning("Cannot create relative path for %s, using absolute path", file_path)
        return str(file_path)
