"""Safe cleanup helpers for downloaded audio and its related files."""

import shutil
from pathlib import Path


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
    """Delete one track, matching sidecars, and folders left without songs.

    The downloads root is never deleted. A folder containing any other
    supported audio is preserved, including an artist folder whose other song
    lives in a different album subfolder.
    """
    download_dir = download_dir.resolve()
    audio_path = audio_path.resolve()
    audio_path.relative_to(download_dir)
    parent = audio_path.parent
    sidecars = [
        candidate
        for candidate in parent.iterdir()
        if candidate.is_file()
        and candidate.stem.casefold() == audio_path.stem.casefold()
        and candidate.suffix.lower() in DOWNLOAD_SIDECAR_EXTENSIONS
    ]

    audio_path.unlink()
    for sidecar in sidecars:
        sidecar.unlink(missing_ok=True)

    audio_extensions = supported_audio_extensions | PRESERVED_AUDIO_EXTENSIONS
    has_other_audio = any(
        candidate.is_file()
        and candidate.suffix.lower() in audio_extensions
        for candidate in parent.rglob("*")
    )
    if parent != download_dir and not has_other_audio:
        shutil.rmtree(parent)
        parent = parent.parent

    while parent != download_dir and parent.exists() and not any(parent.iterdir()):
        next_parent = parent.parent
        parent.rmdir()
        parent = next_parent
