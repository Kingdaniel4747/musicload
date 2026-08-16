"""Download functionality using yt-dlp."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from musicload.config import DEFAULT_FILENAME_TEMPLATE, MAX_FILENAME_BYTES, get_config
from musicload.download_paths import (
    _compute_filename as _compute_filename,
)
from musicload.download_paths import (
    _file_exists as _file_exists,
)
from musicload.download_paths import (
    _find_downloaded_file as _find_downloaded_file,
)
from musicload.download_paths import (
    _get_output_path as _get_output_path,
)
from musicload.download_paths import (
    _get_primary_artist as _get_primary_artist,
)
from musicload.download_paths import (
    _sanitize_path_component as _sanitize_path_component,
)
from musicload.download_paths import (
    _truncate_to_bytes as _truncate_to_bytes,
)
from musicload.download_progress import make_progress_hook
from musicload.library_index import find_existing_download, record_download
from musicload.lyrics import get_lyrics_for_video, save_lyrics
from musicload.tags import write_multi_artist_tags
from musicload.unavailable import (
    is_on_cooldown,
    is_unavailable_error,
    record_unavailable,
)
from musicload.yt_dlp_wrapper import extract_info_with_retry

logger = logging.getLogger(__name__)


class UnavailableCooldownError(Exception):
    """Raised when a video is skipped due to unavailable cooldown."""

    pass


class DownloadCancelledError(Exception):
    """Raised when an active queued download is cancelled by the user."""

    pass


class ExistingDownloadError(Exception):
    """Raised for queue downloads when the requested audio file already exists."""

    def __init__(self, path: Path):
        super().__init__(f"Already downloaded: {path}")
        self.path = path


@dataclass(frozen=True)
class _DownloadOptions:
    output_dir: Path
    audio_format: str
    filename_template: str
    organization_mode: str
    use_primary_artist: bool
    cookie_file: str | None
    progress_callback: callable
    should_cancel: Callable[[], bool] | None


def _check_cooldown(config, video_id: str) -> None:
    cooldown_hours = config.unavailable_cooldown_hours
    if not is_on_cooldown(config.data_dir, video_id, cooldown_hours):
        return
    logger.info("Skipping (unavailable cooldown): %s", video_id)
    raise UnavailableCooldownError(
        f"Video {video_id} is on unavailable cooldown. "
        f"It will be retried after the cooldown period ({cooldown_hours}h) expires."
    )


def _raise_if_cancelled(
    should_cancel: Callable[[], bool] | None, error: Exception
) -> None:
    if should_cancel and should_cancel():
        raise DownloadCancelledError("Download cancelled") from error


def _extract_download_info(config, video_id: str, options: _DownloadOptions) -> dict:
    url = f"https://music.youtube.com/watch?v={video_id}"
    try:
        return extract_info_with_retry(
            ydl_opts={"quiet": True, "no_warnings": True},
            url=url,
            download=False,
            cookie_file=options.cookie_file,
            config=config,
        )
    except Exception as error:
        _raise_if_cancelled(options.should_cancel, error)
        if is_unavailable_error(str(error)):
            record_unavailable(config.data_dir, video_id, str(error))
        raise


def _apply_album_metadata(
    info: dict,
    album: str | None,
    album_artist: str | None,
    album_year: int | None,
    track_number: int | None,
) -> None:
    if not album:
        return
    info["album"] = album
    if album_artist:
        info["artist"] = album_artist
    if album_year:
        info["release_year"] = album_year
    if track_number:
        info["track_number"] = track_number


def _existing_download_path(
    config,
    video_id: str,
    info: dict,
    options: _DownloadOptions,
    title: str,
    artist: str,
    report_existing: bool,
) -> Path | None:
    indexed = find_existing_download(config.data_dir, video_id, info)
    if indexed:
        logger.info("Skipping (library index): %s - %s", artist, title)
        if report_existing:
            raise ExistingDownloadError(indexed)
        return indexed

    existing = _file_exists(
        options.output_dir,
        info,
        options.audio_format,
        options.filename_template,
        options.organization_mode,
        options.use_primary_artist,
    )
    if not existing:
        return None
    logger.info("Skipping (exists): %s - %s", artist, title)
    record_download(config.data_dir, video_id, info, existing)
    if report_existing:
        raise ExistingDownloadError(existing)
    return existing


def _add_cancellation_hook(
    ydl_options: dict, should_cancel: Callable[[], bool] | None
) -> None:
    if not should_cancel:
        return
    original_hooks = ydl_options.get("progress_hooks", [])

    def cancellation_hook(_data):
        if should_cancel():
            raise DownloadCancelledError("Download cancelled")

    ydl_options["progress_hooks"] = [cancellation_hook, *original_hooks]


def _perform_download(
    config,
    video_id: str,
    info: dict,
    options: _DownloadOptions,
    title: str,
    artist: str,
) -> None:
    url = f"https://music.youtube.com/watch?v={video_id}"
    try:
        ydl_options = _get_ydl_opts(
            options.output_dir,
            options.audio_format,
            options.filename_template,
            options.organization_mode,
            info,
            options.progress_callback,
            options.use_primary_artist,
            options.cookie_file,
        )
        _add_cancellation_hook(ydl_options, options.should_cancel)
        extract_info_with_retry(
            ydl_opts=ydl_options,
            url=url,
            download=True,
            cookie_file=options.cookie_file,
            config=config,
        )
    except Exception as error:
        _raise_if_cancelled(options.should_cancel, error)
        if is_unavailable_error(str(error)):
            record_unavailable(
                config.data_dir,
                video_id,
                str(error),
                title=title,
                artist=artist,
            )
        raise


def _postprocess_download(
    config,
    video_id: str,
    info: dict,
    options: _DownloadOptions,
    title: str,
    artist: str,
    duration: int,
    artists: list[str] | None,
    fetch_lyrics: bool,
    apply_replaygain: bool,
) -> Path | None:
    audio_path = _find_downloaded_file(
        options.output_dir,
        info,
        options.audio_format,
        options.filename_template,
        options.organization_mode,
        options.use_primary_artist,
    )
    if not audio_path:
        return None

    record_download(config.data_dir, video_id, info, audio_path)
    if artists:
        write_multi_artist_tags(audio_path, artists)
    if fetch_lyrics:
        lyrics = get_lyrics_for_video(video_id, title, artist, duration)
        if lyrics:
            save_lyrics(lyrics, audio_path)
    if apply_replaygain:
        from musicload.replaygain import apply_replaygain as rsgain_apply

        rsgain_apply(audio_path, options.audio_format)
    return audio_path


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
        opts["progress_hooks"] = [make_progress_hook(progress_callback)]

    return opts


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
    should_cancel: Callable[[], bool] | None = None,
    report_existing: bool = False,
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
    config = get_config()
    _check_cooldown(config, video_id)
    options = _DownloadOptions(
        output_dir=output_dir,
        audio_format=audio_format,
        filename_template=filename_template,
        organization_mode=organization_mode,
        use_primary_artist=use_primary_artist,
        cookie_file=cookie_file,
        progress_callback=progress_callback,
        should_cancel=should_cancel,
    )
    info = _extract_download_info(config, video_id, options)
    title = info.get("title", "Unknown")
    artist = info.get("artist") or info.get("uploader", "Unknown")
    duration = info.get("duration", 0)
    _apply_album_metadata(info, album, album_artist, album_year, track_number)

    existing = _existing_download_path(
        config, video_id, info, options, title, artist, report_existing
    )
    if existing:
        return existing

    logger.info("Downloading: %s - %s", artist, title)
    _perform_download(config, video_id, info, options, title, artist)
    return _postprocess_download(
        config,
        video_id,
        info,
        options,
        title,
        artist,
        duration,
        artists,
        fetch_lyrics,
        apply_replaygain,
    )


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


@dataclass(frozen=True)
class _PlaylistOptions:
    output_dir: Path
    audio_format: str
    filename_template: str
    fetch_lyrics: bool
    organization_mode: str
    use_primary_artist: bool
    cookie_file: str | None
    apply_replaygain: bool


def _download_playlist_entry(
    entry: dict,
    position: int,
    total: int,
    config,
    options: _PlaylistOptions,
) -> tuple[Path | None, bool]:
    video_id = entry.get("id") or entry.get("url", "").split("=")[-1]
    title = entry.get("title", "Unknown")
    artist = entry.get("artist") or entry.get("uploader", "Unknown")
    duration = entry.get("duration", 0)

    if video_id and is_on_cooldown(
        config.data_dir, video_id, config.unavailable_cooldown_hours
    ):
        logger.info(
            "[%d/%d] Skipping (unavailable cooldown): %s - %s",
            position,
            total,
            artist,
            title,
        )
        return None, True

    existing = _file_exists(
        options.output_dir,
        entry,
        options.audio_format,
        options.filename_template,
        options.organization_mode,
        options.use_primary_artist,
    )
    if existing:
        logger.info(
            "[%d/%d] Skipping (exists): %s - %s",
            position,
            total,
            artist,
            title,
        )
        return existing, True

    logger.info("[%d/%d] Downloading: %s - %s", position, total, artist, title)
    try:
        url = f"https://music.youtube.com/watch?v={video_id}"
        ydl_options = _get_ydl_opts(
            options.output_dir,
            options.audio_format,
            options.filename_template,
            options.organization_mode,
            entry,
            None,
            options.use_primary_artist,
            options.cookie_file,
        )
        extract_info_with_retry(
            ydl_opts=ydl_options,
            url=url,
            download=True,
            cookie_file=options.cookie_file,
            config=config,
        )
        audio_path = _find_downloaded_file(
            options.output_dir,
            entry,
            options.audio_format,
            options.filename_template,
            options.organization_mode,
            options.use_primary_artist,
        )
        if not audio_path:
            return None, False
        if options.fetch_lyrics and video_id:
            lyrics = get_lyrics_for_video(video_id, title, artist, duration)
            if lyrics:
                save_lyrics(lyrics, audio_path)
        if options.apply_replaygain:
            from musicload.replaygain import apply_replaygain as rsgain_apply

            rsgain_apply(audio_path, options.audio_format)
        return audio_path, False
    except Exception as error:
        logger.warning("Failed to download %s: %s", title, error)
        if video_id and is_unavailable_error(str(error)):
            record_unavailable(
                config.data_dir,
                video_id,
                str(error),
                title=title,
                artist=artist,
            )
        return None, False


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

    downloaded: list[Path] = []
    skipped = 0
    config = get_config()
    options = _PlaylistOptions(
        output_dir=output_dir,
        audio_format=audio_format,
        filename_template=filename_template,
        fetch_lyrics=fetch_lyrics,
        organization_mode=organization_mode,
        use_primary_artist=use_primary_artist,
        cookie_file=cookie_file,
        apply_replaygain=apply_replaygain,
    )

    for position, entry in enumerate(entries, 1):
        if entry is None:
            continue
        audio_path, was_skipped = _download_playlist_entry(
            entry, position, len(entries), config, options
        )
        if audio_path:
            downloaded.append(audio_path)
        skipped += int(was_skipped)

    new_downloads = len(downloaded) - skipped
    logger.info("Downloaded %d new tracks (%d skipped)", new_downloads, skipped)
    return downloaded
