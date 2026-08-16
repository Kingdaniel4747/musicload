"""Extract embedded or conventional folder artwork from local audio files."""

import base64
from pathlib import Path


def _picture_artwork(audio) -> tuple[bytes, str] | None:
    pictures = getattr(audio, "pictures", None)
    if not pictures:
        return None
    picture = pictures[0]
    return picture.data, picture.mime or "image/jpeg"


def _tag_artwork(tags) -> tuple[bytes, str] | None:
    from mutagen.flac import Picture

    for tag in tags.values():
        if hasattr(tag, "data") and getattr(tag, "mime", "").startswith("image/"):
            return tag.data, tag.mime
    covers = tags.get("covr")
    if covers:
        cover = covers[0] if isinstance(covers, list) else covers
        media_type = (
            "image/png" if getattr(cover, "imageformat", None) == 14 else "image/jpeg"
        )
        return bytes(cover), media_type
    encoded_pictures = tags.get("metadata_block_picture")
    if not encoded_pictures:
        return None
    encoded = (
        encoded_pictures[0]
        if isinstance(encoded_pictures, list)
        else encoded_pictures
    )
    picture = Picture(base64.b64decode(encoded))
    return picture.data, picture.mime or "image/jpeg"


def embedded_artwork(file_path: Path) -> tuple[bytes, str] | None:
    """Return embedded image bytes and media type when available."""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path)
        if audio is None:
            return None
        return _picture_artwork(audio) or (
            _tag_artwork(audio.tags) if audio.tags else None
        )
    except Exception:
        return None
    return None


def folder_artwork(file_path: Path) -> Path | None:
    """Return the first conventional cover-art file beside an audio file."""
    folder_files = {
        child.name.casefold(): child
        for child in file_path.parent.iterdir()
        if child.is_file()
    }
    for name in (
        "cover.jpg",
        "cover.jpeg",
        "cover.png",
        "folder.jpg",
        "folder.jpeg",
        "folder.png",
        "front.jpg",
        "front.jpeg",
        "front.png",
    ):
        if cover_path := folder_files.get(name):
            return cover_path
    return None
