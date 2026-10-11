"""Safe cleanup helpers for downloaded audio and its related files."""

from pathlib import Path
import os


DOWNLOAD_SIDECAR_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".json",
    ".lrc",
    ".png",
    ".txt",
    ".webp",
}
PRESERVED_AUDIO_EXTENSIONS = {
    ".aac",
    ".m4a",
    ".oga",
    ".ogg",
    ".wav",
    ".webm",
}


def delete_track_files(
    audio_path: Path,
    download_dir: Path,
    supported_audio_extensions: set[str],
) -> None:
    """Delete one track, its sidecars and empty folders; preserve other files.

    The downloads root is never deleted. A folder containing any other
    supported audio is preserved, including an artist folder whose other song
    lives in a different album subfolder.
    """
    download_dir = download_dir.resolve()
    audio_path = audio_path.resolve()
    audio_path.relative_to(download_dir)
    parent = audio_path.parent
    audio_extensions = supported_audio_extensions | PRESERVED_AUDIO_EXTENSIONS
    shared_stem = any(
        candidate != audio_path and candidate.is_file()
        and candidate.suffix.lower() in audio_extensions
        and candidate.stem.casefold() == audio_path.stem.casefold()
        for candidate in parent.iterdir()
    )
    sidecars = [
        candidate
        for candidate in parent.iterdir()
        if candidate.is_file()
        and not shared_stem
        and candidate.stem.casefold() in {audio_path.stem.casefold(), audio_path.stem.casefold() + ".info"}
        and candidate.suffix.lower() in DOWNLOAD_SIDECAR_EXTENSIONS
    ]

    audio_path.unlink()
    for sidecar in sidecars:
        sidecar.unlink(missing_ok=True)

    has_other_audio = any(
        candidate.is_file()
        and candidate.suffix.lower() in audio_extensions
        for candidate in parent.rglob("*")
    )
    if parent != download_dir and not has_other_audio:
        # Only conventional album artwork belongs to the last track. Unknown
        # files and other directories must never be recursively discarded.
        for candidate in parent.iterdir():
            if (not candidate.is_symlink() and candidate.is_file()
                and candidate.stem.casefold() in {"cover", "folder", "album", "front"}
                and candidate.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}):
                candidate.unlink()
        for directory, _, _ in os.walk(parent, topdown=False, followlinks=False):
            child = Path(directory)
            if child != parent and not child.is_symlink() and not any(child.iterdir()):
                child.rmdir()

    while parent != download_dir and parent.exists() and not any(parent.iterdir()):
        next_parent = parent.parent
        parent.rmdir()
        parent = next_parent
