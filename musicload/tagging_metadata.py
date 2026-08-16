"""Read, enrich, and write audio-file metadata and cover art."""

import logging
from pathlib import Path

from musicload.tagging_models import FileMetadata, PartialMetadata

logger = logging.getLogger(__name__)


def extract_metadata(file_path: Path) -> FileMetadata | None:
    """Extract title, artist, album, and duration from an audio file via mutagen.

    Returns FileMetadata if extraction succeeded, None if file is unreadable or has no metadata.
    """
    try:
        from mutagen import File

        audio = File(file_path)
        if audio is None:
            logger.debug("Mutagen could not open: %s", file_path)
            return None

        # Extract artist: prefer ARTISTS multi-value tag, fall back to artist
        artist_raw = audio.get("ARTISTS") or audio.get("artists") or audio.get("artist")
        title_raw = audio.get("title")

        if not title_raw or not artist_raw:
            logger.debug("Missing title or artist metadata in: %s", file_path)
            return None

        artist = artist_raw[0] if isinstance(artist_raw, list) else str(artist_raw)
        title = title_raw[0] if isinstance(title_raw, list) else str(title_raw)

        # Extract album (optional)
        album_raw = audio.get("album")
        album = None
        if album_raw:
            album = album_raw[0] if isinstance(album_raw, list) else str(album_raw)

        # Extract duration (mutagen stores as float seconds in info.length)
        duration_seconds = 0
        if audio.info and hasattr(audio.info, "length"):
            duration_seconds = int(audio.info.length)

        return FileMetadata(
            title=str(title),
            artist=str(artist),
            album=album,
            duration_seconds=duration_seconds,
        )

    except Exception as e:
        logger.warning("Failed to extract metadata from %s: %s", file_path, e)
        return None


def _extract_partial_metadata(file_path: Path) -> PartialMetadata | None:
    """Extract whatever metadata is available from an audio file.

    Unlike extract_metadata(), returns partial results even when title/artist is missing.
    Returns None only if mutagen can't open the file at all.
    """
    try:
        from mutagen import File

        audio = File(file_path)
        if audio is None:
            return None

        def _get_tag(key: str) -> str | None:
            raw = audio.get(key)
            if not raw:
                return None
            return str(raw[0]) if isinstance(raw, list) else str(raw)

        artist = _get_tag("ARTISTS") or _get_tag("artists") or _get_tag("artist")
        title = _get_tag("title")
        album = _get_tag("album")

        duration_seconds = 0
        if audio.info and hasattr(audio.info, "length"):
            duration_seconds = int(audio.info.length)

        has_cover = _has_cover_art(file_path)

        return PartialMetadata(
            title=title,
            artist=artist,
            album=album,
            duration_seconds=duration_seconds,
            has_cover=has_cover,
        )

    except Exception as e:
        logger.warning("Failed to extract partial metadata from %s: %s", file_path, e)
        return None


def _has_cover_art(file_path: Path) -> bool:
    """Check if an audio file already has embedded cover art."""
    try:
        from mutagen import File

        audio = File(file_path)
        if audio is None:
            return False

        suffix = file_path.suffix.lower()
        has_cover = False
        if suffix == ".flac":
            from mutagen.flac import FLAC

            has_cover = bool(FLAC(file_path).pictures)
        elif suffix == ".opus":
            has_cover = "metadata_block_picture" in audio
        elif suffix == ".mp3":
            from mutagen.id3 import ID3

            try:
                has_cover = bool(ID3(file_path).getall("APIC"))
            except Exception:
                pass
        return has_cover
    except Exception as e:
        logger.debug("Failed to check cover art for %s: %s", file_path.name, e)
        return False


def _parse_metadata_from_path(
    file_path: Path,
    root_dir: Path,
) -> tuple[str, str, str | None]:
    """Parse title, artist, and album from the file path relative to root_dir.

    Handles:
      - Flat mode: "Artist - Title.opus" -> (title, artist, None)
      - Album mode: "Artist/Album/01 - Title.opus" -> (title, artist, album)
      - Fallback: "Title.opus" -> (title, "", None)
    """
    try:
        rel = file_path.relative_to(root_dir)
    except ValueError:
        rel = file_path

    parts = rel.parts
    stem = file_path.stem

    # Album mode: Artist/Album/TrackNum - Title.ext
    if len(parts) >= 3:
        artist = parts[-3] if len(parts) >= 3 else parts[0]
        album = parts[-2]
        title = stem
        # Strip leading track number pattern like "01 - "
        if " - " in title:
            title = title.split(" - ", 1)[1]
        return title, artist, album

    # Flat mode: Artist - Title.ext
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip(), artist.strip(), None

    return stem, "", None


def _search_metadata(title: str, artist: str):
    """Search YouTube Music for a track matching the given title and artist.

    Returns the top Track result or None.
    """
    from musicload.search import search

    query = f"{title} {artist}" if artist else title
    try:
        results = search(query, limit=1)
        return results[0] if results else None
    except Exception as e:
        logger.warning("YouTube Music search failed for '%s': %s", query, e)
        return None


def _write_metadata_tags(
    file_path: Path,
    track,
    partial: PartialMetadata,
) -> bool:
    """Write missing metadata tags to an audio file from a YouTube Music Track result.

    Only writes tags that are currently missing (doesn't overwrite existing).
    Returns True if any tags were written.
    """
    try:
        from mutagen import File

        audio = File(file_path)
        if audio is None:
            return False

        suffix = file_path.suffix.lower()
        wrote_any = False

        if suffix in (".opus", ".flac"):
            wrote_any = _write_vorbis_tags(file_path, audio, track, partial)
        elif suffix == ".mp3":
            wrote_any = _write_id3_tags(file_path, audio, track, partial)

        # Write multi-artist tags if track has multiple artists
        if track.artists and len(track.artists) > 1:
            from musicload.tags import write_multi_artist_tags

            write_multi_artist_tags(file_path, track.artists)
            wrote_any = True

        # Embed cover art if missing
        if not partial.has_cover and track.thumbnail_url:
            if _embed_cover_art(file_path, track.thumbnail_url):
                wrote_any = True

        return wrote_any

    except Exception as e:
        logger.warning("Failed to write metadata tags to %s: %s", file_path.name, e)
        return False


def _write_vorbis_tags(file_path: Path, audio, track, partial: PartialMetadata) -> bool:
    """Write missing Vorbis comment tags (Opus/FLAC)."""
    wrote = False
    if not partial.title and track.title:
        audio["title"] = [track.title]
        wrote = True
    if not partial.artist and track.artist:
        audio["artist"] = [track.artist]
        wrote = True
    if not partial.album and track.album:
        audio["album"] = [track.album]
        wrote = True
    if wrote:
        audio.save()
    return wrote


def _write_id3_tags(file_path: Path, audio, track, partial: PartialMetadata) -> bool:
    """Write missing ID3 tags (MP3)."""
    from mutagen.id3 import ID3, TALB, TIT2, TPE1, ID3NoHeaderError

    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        tags = ID3()

    wrote = False
    if not partial.title and track.title:
        tags.add(TIT2(encoding=3, text=[track.title]))
        wrote = True
    if not partial.artist and track.artist:
        tags.add(TPE1(encoding=3, text=[track.artist]))
        wrote = True
    if not partial.album and track.album:
        tags.add(TALB(encoding=3, text=[track.album]))
        wrote = True
    if wrote:
        tags.save(file_path)
    return wrote


def _embed_cover_art(file_path: Path, thumbnail_url: str) -> bool:
    """Download thumbnail and embed as cover art."""
    import urllib.request

    try:
        with urllib.request.urlopen(thumbnail_url, timeout=15) as resp:  # noqa: S310
            image_data = resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")

        suffix = file_path.suffix.lower()

        if suffix == ".flac":
            return _embed_cover_flac(file_path, image_data, content_type)
        elif suffix == ".opus":
            return _embed_cover_opus(file_path, image_data, content_type)
        elif suffix == ".mp3":
            return _embed_cover_mp3(file_path, image_data, content_type)

        return False
    except Exception as e:
        logger.warning("Failed to embed cover art for %s: %s", file_path.name, e)
        return False


def _embed_cover_flac(file_path: Path, image_data: bytes, content_type: str) -> bool:
    from mutagen.flac import FLAC, Picture

    audio = FLAC(file_path)
    pic = Picture()
    pic.data = image_data
    pic.type = 3  # Cover (front)
    pic.mime = content_type
    audio.add_picture(pic)
    audio.save()
    return True


def _embed_cover_opus(file_path: Path, image_data: bytes, content_type: str) -> bool:
    import base64
    import struct

    from mutagen.oggopus import OggOpus

    audio = OggOpus(file_path)

    # Build a FLAC Picture block manually for metadata_block_picture
    # Format: type(4) + mime_len(4) + mime + desc_len(4) + desc + width(4) + height(4) + depth(4) + colors(4) + data_len(4) + data
    mime_bytes = content_type.encode("utf-8")
    desc_bytes = b""
    picture_block = struct.pack(
        ">II",
        3,
        len(mime_bytes),  # type=Cover(front), mime length
    )
    picture_block += mime_bytes
    picture_block += struct.pack(">I", len(desc_bytes))  # description length
    picture_block += desc_bytes
    picture_block += struct.pack(">IIII", 0, 0, 0, 0)  # width, height, depth, colors
    picture_block += struct.pack(">I", len(image_data))  # data length
    picture_block += image_data

    audio["metadata_block_picture"] = [base64.b64encode(picture_block).decode("ascii")]
    audio.save()
    return True


def _embed_cover_mp3(file_path: Path, image_data: bytes, content_type: str) -> bool:
    from mutagen.id3 import APIC, ID3, ID3NoHeaderError

    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.add(
        APIC(
            encoding=3,
            mime=content_type,
            type=3,  # Cover (front)
            desc="Cover",
            data=image_data,
        )
    )
    tags.save(file_path)
    return True
