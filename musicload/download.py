"""Download functionality using yt-dlp."""

import logging
from pathlib import Path

import yt_dlp

from musicload.config import DEFAULT_FILENAME_TEMPLATE, MAX_FILENAME_BYTES, get_config
from musicload.library_index import find_existing_download, record_download
from musicload.lyrics import get_lyrics_for_video, save_lyrics
from musicload.tags import write_multi_artist_tags
from musicload.unavailable import is_on_cooldown, is_unavailable_error, record_unavailable
from musicload.yt_dlp_wrapper import extract_info_with_retry

logger = logging.getLogger(__name__)


class UnavailableCooldownError(Exception):
    """Raised when a video is skipped due to unavailable cooldown."""

    pass


class DownloadCancelledError(Exception):
    """Raised when an active queued download is cancelled by the user."""

    pass


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


def _get_ydl_opts(
    output_dir: Path,
    audio_format: str,
    filename_template: str,
    organization_mode: str,
    info: dict,
    progress_callback: callable = None,
    use_primary_artist: bool = False,
    cookie_file: str | None = None,
) -> dict:
    """Get common yt-dlp options."""
    # Calculate output path based on organization mode
    output_path = _get_output_path(
        output_dir, info, filename_template, organization_mode, use_primary_artist
    )

    opts = {
        "format": f"bestaudio[ext={audio_format}]/bestaudio[acodec*={audio_format}]/bestaudio/best",
        "outtmpl": output_path,
        "color": "never",
        "trim_file_name": MAX_FILENAME_BYTES,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format,
                "preferredquality": "0",
            },
            {
                "key": "FFmpegMetadata",
                "add_metadata": True,
            },
            {
                "key": "EmbedThumbnail",
            },
        ],
        "writethumbnail": True,
        "quiet": True,
        "no_warnings": True,
        "retry_sleep_functions": {
            "http": lambda n: min(2**n, 30),  # Cap at 30s
            "fragment": lambda n: min(2**n, 30),
        },
        # Docker/minimal environments may cause yt-dlp to choose a narrower default
        # YouTube client set. Pin a broader, cookie-free client mix for consistency.
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web", "web_safari"],
            }
        },
        "remote_components": ["ejs:github"],
    }

    # Note: cookies are now handled by yt_dlp_wrapper, not here

    if progress_callback:

        def progress_hook(d):
            """yt-dlp progress hook."""
            if d["status"] == "downloading":
                # Extract progress information
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                speed = d.get("speed", 0)
                eta = d.get("eta", 0)

                # Calculate percentage
                percent = (downloaded / total * 100) if total > 0 else 0

                # Format speed
                if speed:
                    if speed > 1024 * 1024:
                        speed_str = f"{speed / 1024 / 1024:.1f} MB/s"
                    elif speed > 1024:
                        speed_str = f"{speed / 1024:.1f} KB/s"
                    else:
                        speed_str = f"{speed:.0f} B/s"
                else:
                    speed_str = "N/A"

                # Format ETA
                if eta:
                    if eta > 3600:
                        eta_str = f"{int(eta) // 3600}h {(int(eta) % 3600) // 60}m"
                    elif eta > 60:
                        eta_str = f"{int(eta) // 60}m {int(eta) % 60}s"
                    else:
                        eta_str = f"{int(eta)}s"
                else:
                    eta_str = "N/A"

                progress_callback(
                    {
                        "downloaded_bytes": downloaded,
                        "total_bytes": total,
                        "percent": percent,
                        "speed": speed_str,
                        "eta": eta_str,
                    }
                )

        opts["progress_hooks"] = [progress_hook]

    return opts


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


def download(
    video_id: str,
    output_dir: Path,
    audio_format: str = "opus",
    filename_template: str = DEFAULT_FILENAME_TEMPLATE,
    fetch_lyrics: bool = True,
    progress_callback: callable = None,
    organization_mode: str = "flat",
    use_primary_artist: bool = False,
    cookie_file: str | None = None,
    artists: list[str] | None = None,
    apply_replaygain: bool = False,
    album: str | None = None,
    album_artist: str | None = None,
    album_year: int | None = None,
    track_number: int | None = None,
    should_cancel: callable | None = None,
) -> Path:
    """
    Download a track from YouTube Music.

    Args:
        video_id: YouTube video ID
        output_dir: Directory to save the downloaded file
        audio_format: Audio format (opus, mp3, flac)
        filename_template: yt-dlp output template for filename (flat mode only)
        fetch_lyrics: Whether to fetch and save lyrics
        progress_callback: Optional callback for progress updates
        organization_mode: "flat" or "album" organization
        use_primary_artist: Extract primary artist for folder (before feat., &, etc.)
        artists: List of individual artist names for multi-value tags (optional)

    Returns:
        Path to the downloaded audio file

    Raises:
        Exception: If download fails (unavailable errors are recorded before re-raising)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check if video is on unavailable cooldown before hitting YouTube
    config = get_config()
    cooldown_hours = config.unavailable_cooldown_hours
    if is_on_cooldown(config.data_dir, video_id, cooldown_hours):
        logger.info("Skipping (unavailable cooldown): %s", video_id)
        raise UnavailableCooldownError(
            f"Video {video_id} is on unavailable cooldown. "
            f"It will be retried after the cooldown period ({cooldown_hours}h) expires."
        )

    url = f"https://music.youtube.com/watch?v={video_id}"

    try:
        # Extract info first to get metadata
        ydl_opts_info = {"quiet": True, "no_warnings": True}
        info = extract_info_with_retry(
            ydl_opts=ydl_opts_info,
            url=url,
            download=False,
            cookie_file=cookie_file,
            config=config,
        )
    except Exception as e:
        if should_cancel and should_cancel():
            raise DownloadCancelledError("Download cancelled") from e
        # Record unavailable videos for cooldown, then re-raise
        if is_unavailable_error(str(e)):
            record_unavailable(config.data_dir, video_id, str(e))
        raise

    title = info.get("title", "Unknown")
    artist = info.get("artist") or info.get("uploader", "Unknown")
    duration = info.get("duration", 0)

    # Album downloads pass canonical metadata from the album page. A per-video
    # lookup often lacks this context, which would otherwise split one album
    # into a separate folder for every track.
    if album:
        info["album"] = album
        if album_artist:
            info["artist"] = album_artist
        if album_year:
            info["release_year"] = album_year
        if track_number:
            info["track_number"] = track_number

    # Fallback: YouTube liefert für manche Tracks kein Album (z. B. Feature-Songs).
    # Statt leer zu lassen, nehmen wir den Songtitel als Pseudo-Album.
    # Keep missing album metadata empty: individual tracks belong directly
    # in their artist directory, while real album downloads provide album.

    # The index catches the same recording even when it came from another
    # YouTube video, album page, playlist, or earlier cron run.
    indexed = find_existing_download(config.data_dir, video_id, info)
    if indexed:
        logger.info("Skipping (library index): %s - %s", artist, title)
        return indexed

    # Check if already downloaded
    existing = _file_exists(
        output_dir,
        info,
        audio_format,
        filename_template,
        organization_mode,
        use_primary_artist,
    )
    if existing:
        logger.info("Skipping (exists): %s - %s", artist, title)
        record_download(config.data_dir, video_id, info, existing)
        return existing

    logger.info("Downloading: %s - %s", artist, title)

    try:
        # Download the track
        ydl_opts = _get_ydl_opts(
            output_dir,
            audio_format,
            filename_template,
            organization_mode,
            info,
            progress_callback,
            use_primary_artist,
            cookie_file,
        )
        if should_cancel:
            original_hooks = ydl_opts.get("progress_hooks", [])

            def cancellation_hook(_data):
                if should_cancel():
                    raise DownloadCancelledError("Download cancelled")

            ydl_opts["progress_hooks"] = [cancellation_hook, *original_hooks]
        extract_info_with_retry(
            ydl_opts=ydl_opts,
            url=url,
            download=True,
            cookie_file=cookie_file,
            config=config,
        )
    except Exception as e:
        if should_cancel and should_cancel():
            raise DownloadCancelledError("Download cancelled") from e
        # Record unavailable videos for cooldown, then re-raise
        if is_unavailable_error(str(e)):
            record_unavailable(config.data_dir, video_id, str(e), title=title, artist=artist)
        raise

    # Find the downloaded file
    audio_path = _find_downloaded_file(
        output_dir,
        info,
        audio_format,
        filename_template,
        organization_mode,
        use_primary_artist,
    )

    if audio_path:
        record_download(config.data_dir, video_id, info, audio_path)

        # Write multi-valued ARTISTS/ALBUMARTISTS tags if artists provided
        if artists:
            write_multi_artist_tags(audio_path, artists)

        # Fetch and save lyrics using ytmusicapi metadata for better matching
        if fetch_lyrics:
            lyrics = get_lyrics_for_video(video_id, title, artist, duration)
            if lyrics:
                save_lyrics(lyrics, audio_path)

        # Apply ReplayGain/R128 tags
        if apply_replaygain:
            from musicload.replaygain import apply_replaygain as rsgain_apply

            rsgain_apply(audio_path, audio_format)

    return audio_path


def _extract_video_id_from_url(url: str) -> str | None:
    """Extract YouTube video ID from a URL, if possible.

    Handles youtube.com/watch?v=ID and music.youtube.com/watch?v=ID formats.
    Returns None for playlists or unrecognized URLs.
    """
    import re

    match = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", url)
    return match.group(1) if match else None


def download_url(
    url: str,
    output_dir: Path,
    audio_format: str = "opus",
    filename_template: str = DEFAULT_FILENAME_TEMPLATE,
    fetch_lyrics: bool = True,
    organization_mode: str = "flat",
    use_primary_artist: bool = False,
    cookie_file: str | None = None,
    apply_replaygain: bool = False,
) -> Path | list[Path]:
    """
    Download a track or playlist from a YouTube/YouTube Music URL.

    Args:
        url: YouTube or YouTube Music URL (single track or playlist)
        output_dir: Directory to save the downloaded file(s)
        audio_format: Audio format (opus, mp3, flac)
        filename_template: yt-dlp output template for filename
        fetch_lyrics: Whether to fetch and save lyrics
        organization_mode: "flat" or "album" organization
        use_primary_artist: Extract primary artist for folder (before feat., &, etc.)

    Returns:
        Path to downloaded file, or list of Paths for playlists
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Extract info first to check if it's a playlist
        ydl_opts_info = {"quiet": True, "no_warnings": True}
        info = extract_info_with_retry(
            ydl_opts=ydl_opts_info,
            url=url,
            download=False,
            cookie_file=cookie_file,
            config=get_config(),
        )
    except Exception as e:
        # Record unavailable videos for cooldown, then re-raise
        video_id = _extract_video_id_from_url(url)
        if video_id and is_unavailable_error(str(e)):
            record_unavailable(get_config().data_dir, video_id, str(e))
        raise

    # Check if this is a playlist
    if info.get("_type") == "playlist" or "entries" in info:
        return _download_playlist(
            info,
            output_dir,
            audio_format,
            filename_template,
            fetch_lyrics,
            organization_mode,
            use_primary_artist,
            cookie_file,
            apply_replaygain=apply_replaygain,
        )

    # Single track
    return _download_single(
        url,
        info,
        output_dir,
        audio_format,
        filename_template,
        fetch_lyrics,
        organization_mode,
        use_primary_artist,
        cookie_file,
        apply_replaygain=apply_replaygain,
    )


def _download_single(
    url: str,
    info: dict,
    output_dir: Path,
    audio_format: str,
    filename_template: str,
    fetch_lyrics: bool,
    organization_mode: str,
    use_primary_artist: bool = False,
    cookie_file: str | None = None,
    apply_replaygain: bool = False,
) -> Path:
    """Download a single track.

    Records unavailable videos for cooldown before re-raising errors.
    """
    title = info.get("title", "Unknown")
    artist = info.get("artist") or info.get("uploader", "Unknown")
    duration = info.get("duration", 0)
    video_id = info.get("id")

    # Check if already downloaded
    existing = _file_exists(
        output_dir,
        info,
        audio_format,
        filename_template,
        organization_mode,
        use_primary_artist,
    )
    if existing:
        logger.info("Skipping (exists): %s - %s", artist, title)
        return existing

    logger.info("Downloading: %s - %s", artist, title)

    try:
        ydl_opts = _get_ydl_opts(
            output_dir,
            audio_format,
            filename_template,
            organization_mode,
            info,
            None,
            use_primary_artist,
            cookie_file,
        )
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        # Record unavailable videos for cooldown, then re-raise
        if video_id and is_unavailable_error(str(e)):
            record_unavailable(get_config().data_dir, video_id, str(e), title=title, artist=artist)
        raise

    audio_path = _find_downloaded_file(
        output_dir,
        info,
        audio_format,
        filename_template,
        organization_mode,
        use_primary_artist,
    )

    if audio_path and fetch_lyrics:
        lyrics = (
            get_lyrics_for_video(video_id, title, artist, duration)
            if video_id
            else None
        )
        if lyrics:
            save_lyrics(lyrics, audio_path)

    if audio_path and apply_replaygain:
        from musicload.replaygain import apply_replaygain as rsgain_apply

        rsgain_apply(audio_path, audio_format)

    return audio_path


def _download_playlist(
    info: dict,
    output_dir: Path,
    audio_format: str,
    filename_template: str,
    fetch_lyrics: bool,
    organization_mode: str,
    use_primary_artist: bool = False,
    cookie_file: str | None = None,
    apply_replaygain: bool = False,
) -> list[Path]:
    """Download all tracks from a playlist."""
    entries = info.get("entries", [])
    playlist_title = info.get("title", "Unknown Playlist")

    logger.info("Downloading playlist: %s (%d tracks)", playlist_title, len(entries))

    downloaded = []
    skipped = 0

    config = get_config()
    cooldown_hours = config.unavailable_cooldown_hours

    for i, entry in enumerate(entries, 1):
        if entry is None:
            continue

        video_id = entry.get("id") or entry.get("url", "").split("=")[-1]
        title = entry.get("title", "Unknown")
        artist = entry.get("artist") or entry.get("uploader", "Unknown")
        duration = entry.get("duration", 0)

        # Check if video is on unavailable cooldown
        if video_id and is_on_cooldown(config.data_dir, video_id, cooldown_hours):
            logger.info(
                "[%d/%d] Skipping (unavailable cooldown): %s - %s",
                i,
                len(entries),
                artist,
                title,
            )
            skipped += 1
            continue

        # Check if already downloaded
        existing = _file_exists(
            output_dir,
            entry,
            audio_format,
            filename_template,
            organization_mode,
            use_primary_artist,
        )
        if existing:
            logger.info(
                "[%d/%d] Skipping (exists): %s - %s", i, len(entries), artist, title
            )
            downloaded.append(existing)
            skipped += 1
            continue

        logger.info("[%d/%d] Downloading: %s - %s", i, len(entries), artist, title)

        try:
            url = f"https://music.youtube.com/watch?v={video_id}"
            ydl_opts = _get_ydl_opts(
                output_dir,
                audio_format,
                filename_template,
                organization_mode,
                entry,
                None,
                use_primary_artist,
                cookie_file,
            )
            extract_info_with_retry(
                ydl_opts=ydl_opts,
                url=url,
                download=True,
                cookie_file=cookie_file,
                config=config,
            )

            audio_path = _find_downloaded_file(
                output_dir,
                entry,
                audio_format,
                filename_template,
                organization_mode,
                use_primary_artist,
            )

            if audio_path:
                downloaded.append(audio_path)
                if fetch_lyrics and video_id:
                    lyrics = get_lyrics_for_video(video_id, title, artist, duration)
                    if lyrics:
                        save_lyrics(lyrics, audio_path)
                if apply_replaygain:
                    from musicload.replaygain import apply_replaygain as rsgain_apply

                    rsgain_apply(audio_path, audio_format)

        except Exception as e:
            logger.warning("Failed to download %s: %s", title, e)
            # Record unavailable videos for cooldown
            if video_id and is_unavailable_error(str(e)):
                record_unavailable(
                    config.data_dir, video_id, str(e), title=title, artist=artist
                )

    new_downloads = len(downloaded) - skipped
    logger.info("Downloaded %d new tracks (%d skipped)", new_downloads, skipped)
    return downloaded


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
        # Existing flat mode logic
        expected_name = _compute_filename(info, filename_template)

        for ext in [audio_format, "opus", "m4a", "webm"]:
            # Check exact match first
            exact_path = output_dir / f"{expected_name}.{ext}"
            if exact_path.exists():
                return exact_path

            # Check with glob for partial matches
            matches = list(output_dir.glob(f"{expected_name[:50]}*.{ext}"))
            if matches:
                return matches[0]

        # Fallback: return most recently modified audio file
        audio_extensions = ["opus", "mp3", "m4a", "flac", "webm"]
        all_audio = []
        for ext in audio_extensions:
            all_audio.extend(output_dir.glob(f"*.{ext}"))

        if all_audio:
            return max(all_audio, key=lambda p: p.stat().st_mtime)

        return None

    # Album mode: search in artist/album subdirectories
    artist = info.get("artist") or info.get("uploader", "Unknown Artist")
    if use_primary_artist:
        artist = _get_primary_artist(artist)
    artist = _sanitize_path_component(artist)
    artist_dir = output_dir / artist

    if not artist_dir.exists():
        return None

    # Get all audio files in artist directory
    audio_extensions = ["opus", "mp3", "m4a", "flac", "webm"]
    all_audio = []
    for ext in audio_extensions:
        all_audio.extend(artist_dir.rglob(f"*.{ext}"))

    # Return most recently modified file (should be the just-downloaded one)
    if all_audio:
        return max(all_audio, key=lambda p: p.stat().st_mtime)

    return None
