"""Command-line interface for Musicload."""

import logging
import os
import shutil
from errno import EXDEV
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from musicload.config import get_config
from musicload.cron.cli import cron_command
from musicload.download import UnavailableCooldownError, download, download_url
from musicload.plugins.cli import plugins_app
from musicload.search import (
    get_charts,
    get_mood_categories,
    get_mood_playlists,
    get_playlist_tracks,
    search,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)


class AudioFormat(str, Enum):
    opus = "opus"
    mp3 = "mp3"
    flac = "flac"


class OrganizationMode(str, Enum):
    flat = "flat"
    album = "album"


class CookieMode(str, Enum):
    auto = "auto"
    always = "always"
    never = "never"


app = typer.Typer(help="Musicload - Search and download music from YouTube Music.")

explore_app = typer.Typer(help="Explore moods, genres, and charts on YouTube Music.")
app.add_typer(explore_app, name="explore")

app.add_typer(plugins_app, name="plugins")

app.command(name="cron")(cron_command)


def _move_path(src: Path, dst: Path) -> None:
    """Move src to dst, supporting cross-filesystem migrations."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dst)
        return
    except OSError as exc:
        if exc.errno != EXDEV:
            raise

    if src.is_dir():
        shutil.copytree(src, dst)
        shutil.rmtree(src)
    else:
        shutil.copy2(src, dst)
        src.unlink()


def _migrate_legacy_data_dir(data_dir: Path) -> None:
    """Migrate legacy <download_dir>/.musicload into an explicit data_dir."""
    legacy_data_dir = Path(os.getenv("MUSICLOAD_DOWNLOAD_DIR", "./downloads")) / ".musicload"
    if legacy_data_dir.resolve() == data_dir.resolve():
        return
    if not legacy_data_dir.exists() or not legacy_data_dir.is_dir():
        return

    logger = logging.getLogger(__name__)
    if data_dir.exists():
        if not data_dir.is_dir():
            logger.warning(
                "Skipping legacy data migration because target is not a directory: %s",
                data_dir,
            )
            return
        if any(data_dir.iterdir()):
            logger.info(
                "Skipping legacy data migration because target is not empty: %s",
                data_dir,
            )
            return
        for child in legacy_data_dir.iterdir():
            _move_path(child, data_dir / child.name)
        legacy_data_dir.rmdir()
        logger.info("Migrated legacy data from %s to %s", legacy_data_dir, data_dir)
        return

    _move_path(legacy_data_dir, data_dir)
    logger.info("Migrated legacy data from %s to %s", legacy_data_dir, data_dir)


def _version_callback(value: bool):
    if value:
        from importlib.metadata import version

        typer.echo(f"musicload, version {version('musicload')}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    cookie_mode: Annotated[
        CookieMode | None,
        typer.Option(
            "--cookie-mode",
            envvar="MUSICLOAD_COOKIE_MODE",
            help="Cookie usage mode: auto, always, never. Default: auto",
        ),
    ] = None,
    cookie_retry_delay: Annotated[
        float | None,
        typer.Option(
            "--cookie-retry-delay",
            envvar="MUSICLOAD_COOKIE_RETRY_DELAY",
            help="Delay in seconds before retrying with cookies. Default: 1.0",
        ),
    ] = None,
    no_log_cookie_usage: Annotated[
        bool,
        typer.Option(
            "--no-log-cookie-usage",
            help="Disable logging of cookie usage statistics",
        ),
    ] = False,
    unavailable_cooldown: Annotated[
        int | None,
        typer.Option(
            "--unavailable-cooldown",
            envvar="MUSICLOAD_UNAVAILABLE_COOLDOWN_HOURS",
            help="Hours to wait before retrying unavailable videos (0 = disabled). Default: 168 (7 days)",
        ),
    ] = None,
    lyrics_cache_hours: Annotated[
        int | None,
        typer.Option(
            "--lyrics-cache-hours",
            envvar="MUSICLOAD_LYRICS_CACHE_HOURS",
            help="Hours to cache negative lyrics lookups (0 = no expiry). Default: 168 (7 days)",
        ),
    ] = None,
    data_dir: Annotated[
        Path | None,
        typer.Option(
            "--data-dir",
            envvar="MUSICLOAD_DATA_DIR",
            help="Data directory for state, cache, and metadata files. Default: <download-dir>/.musicload",
        ),
    ] = None,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = None,
):
    """Musicload - Search and download music from YouTube Music."""
    ctx.ensure_object(dict)

    if cookie_mode is not None:
        os.environ["MUSICLOAD_COOKIE_MODE"] = cookie_mode.value
    if cookie_retry_delay is not None:
        os.environ["MUSICLOAD_COOKIE_RETRY_DELAY"] = str(cookie_retry_delay)
    if no_log_cookie_usage:
        os.environ["MUSICLOAD_LOG_COOKIE_USAGE"] = "false"
    if unavailable_cooldown is not None:
        os.environ["MUSICLOAD_UNAVAILABLE_COOLDOWN_HOURS"] = str(unavailable_cooldown)
    if lyrics_cache_hours is not None:
        os.environ["MUSICLOAD_LYRICS_CACHE_HOURS"] = str(lyrics_cache_hours)
    if data_dir is not None:
        try:
            _migrate_legacy_data_dir(data_dir)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to migrate legacy data directory to %s: %s",
                data_dir,
                exc,
            )
        os.environ["MUSICLOAD_DATA_DIR"] = str(data_dir)


@app.command(name="search")
def search_cmd(
    query: Annotated[str, typer.Argument(help="Search query")],
    limit: Annotated[
        int, typer.Option("-l", "--limit", help="Maximum number of results")
    ] = 10,
):
    """Search for music on YouTube Music."""
    results = search(query, limit=limit)

    if not results:
        typer.echo("No results found.")
        return

    typer.echo(f"\nFound {len(results)} results:\n")

    for i, track in enumerate(results, 1):
        album_info = f" [{track.album}]" if track.album else ""
        typer.echo(f"{i:2}. {track.title} - {track.artist}{album_info}")
        typer.echo(f"    ID: {track.video_id}  Duration: {track.duration_display}")
        typer.echo()


@app.command(name="download")
def download_cmd(
    video_id: Annotated[
        str | None, typer.Argument(help="YouTube video ID")
    ] = None,
    url: Annotated[
        str | None,
        typer.Option("--url", "-u", help="YouTube, YouTube Music, or Deezer URL"),
    ] = None,
    query: Annotated[
        str | None,
        typer.Option("--query", "-q", help="Search query (downloads first match)"),
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output directory")
    ] = None,
    audio_format: Annotated[
        AudioFormat | None,
        typer.Option("--format", "-f", help="Audio format (default: opus)"),
    ] = None,
    filename_template: Annotated[
        str | None,
        typer.Option(
            "--filename",
            "-n",
            help="Filename template (default: '%(artist,uploader)s - %(title)s')",
        ),
    ] = None,
    no_lyrics: Annotated[
        bool, typer.Option("--no-lyrics", help="Skip fetching lyrics")
    ] = False,
    playlist_name: Annotated[
        str | None,
        typer.Option(
            "--add-to-playlist",
            "-p",
            help="Add downloaded track(s) to M3U playlist",
        ),
    ] = None,
    organization_mode: Annotated[
        OrganizationMode | None,
        typer.Option(
            "--organization-mode",
            envvar="MUSICLOAD_ORGANIZATION_MODE",
            help="File organization: flat (all in one dir) or album (Artist/Year - Album/Track). Default: flat",
        ),
    ] = None,
    use_primary_artist: Annotated[
        bool | None,
        typer.Option(
            "--use-primary-artist/--no-use-primary-artist",
            help="Use only primary artist for folder names in album mode (strips 'feat.', etc.)",
        ),
    ] = None,
    replaygain: Annotated[
        bool | None,
        typer.Option(
            "--replaygain/--no-replaygain",
            help="Apply ReplayGain/R128 loudness normalization tags (requires rsgain)",
        ),
    ] = None,
    allow_ugc: Annotated[
        bool | None,
        typer.Option(
            "--allow-ugc/--no-allow-ugc",
            help="Include UGC (user-generated content) tracks in playlist/chart results. Default: exclude",
        ),
    ] = None,
):
    """Download a track by video ID, URL, or search query.

    Examples:

      musicload download VIDEO_ID

      musicload download --url "https://music.youtube.com/watch?v=..."

      musicload download --url "https://music.youtube.com/playlist?list=..."

      musicload download --url "https://www.deezer.com/playlist/..."

      musicload download --query "Bohemian Rhapsody Queen"
    """
    if not video_id and not url and not query:
        typer.echo("Error: One of VIDEO_ID, --url, or --query is required", err=True)
        raise typer.Exit(code=2)

    if replaygain is not None:
        os.environ["MUSICLOAD_REPLAYGAIN"] = "true" if replaygain else "false"
    if allow_ugc is not None:
        os.environ["MUSICLOAD_ALLOW_UGC"] = "true" if allow_ugc else "false"

    config = get_config()
    output_dir = output if output else config.download_dir
    fmt = audio_format.value if audio_format else config.audio_format
    template = filename_template or config.filename_template
    org_mode = organization_mode.value if organization_mode else config.organization_mode
    primary_artist = use_primary_artist if use_primary_artist is not None else config.use_primary_artist

    try:
        # Search and download first match
        if query:
            results = search(query, limit=1)
            if not results:
                typer.echo(f"Error: No results found for: {query}", err=True)
                raise typer.Exit(code=1)

            track = results[0]
            typer.echo(f"Found: {track.title} - {track.artist}")

            audio_path = download(
                video_id=track.video_id,
                output_dir=output_dir,
                audio_format=fmt,
                filename_template=template,
                fetch_lyrics=not no_lyrics,
                organization_mode=org_mode,
                use_primary_artist=primary_artist,
                apply_replaygain=config.replaygain,
            )
            if audio_path:
                typer.echo(f"Downloaded: {audio_path}")
                if playlist_name:
                    from musicload.playlist import add_to_m3u

                    add_to_m3u([audio_path], playlist_name, output_dir)
                    typer.echo(f"Added to playlist: {playlist_name}.m3u")
            return

        # Handle URL (YouTube, YouTube Music, or Deezer)
        if url:
            from musicload.deezer import get_tracks_from_url as get_deezer_tracks_from_url
            from musicload.deezer import is_deezer_url

            if is_deezer_url(url):
                _download_external_url(
                    url=url,
                    output_dir=output_dir,
                    audio_format=fmt,
                    filename_template=template,
                    fetch_lyrics=not no_lyrics,
                    playlist_name=playlist_name,
                    organization_mode=org_mode,
                    use_primary_artist=primary_artist,
                    source_name="Deezer playlist",
                    get_tracks_from_url=get_deezer_tracks_from_url,
                    apply_replaygain=config.replaygain,
                )
            else:
                result = download_url(
                    url=url,
                    output_dir=output_dir,
                    audio_format=fmt,
                    filename_template=template,
                    fetch_lyrics=not no_lyrics,
                    organization_mode=org_mode,
                    use_primary_artist=primary_artist,
                    apply_replaygain=config.replaygain,
                )

                if isinstance(result, list):
                    typer.echo(f"Downloaded {len(result)} tracks to {output_dir}")
                    if playlist_name and result:
                        from musicload.playlist import add_to_m3u

                        add_to_m3u(result, playlist_name, output_dir)
                        typer.echo(f"Added {len(result)} track(s) to playlist: {playlist_name}.m3u")
                elif result:
                    typer.echo(f"Downloaded: {result}")
                    if playlist_name:
                        from musicload.playlist import add_to_m3u

                        add_to_m3u([result], playlist_name, output_dir)
                        typer.echo(f"Added to playlist: {playlist_name}.m3u")
                else:
                    typer.echo("Download completed but could not locate file.")
            return

        # Download by video ID
        audio_path = download(
            video_id=video_id,
            output_dir=output_dir,
            audio_format=fmt,
            filename_template=template,
            fetch_lyrics=not no_lyrics,
            organization_mode=org_mode,
            use_primary_artist=primary_artist,
            apply_replaygain=config.replaygain,
        )

        if audio_path:
            typer.echo(f"Downloaded: {audio_path}")
            if playlist_name:
                from musicload.playlist import add_to_m3u

                add_to_m3u([audio_path], playlist_name, output_dir)
                typer.echo(f"Added to playlist: {playlist_name}.m3u")
        else:
            typer.echo("Download completed but could not locate file.")

    except UnavailableCooldownError as e:
        typer.echo(str(e))
        return
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)


def _download_external_url(
    url: str,
    output_dir: Path,
    audio_format: str,
    filename_template: str,
    fetch_lyrics: bool,
    playlist_name: str | None = None,
    organization_mode: str = "flat",
    use_primary_artist: bool = False,
    source_name: str = "External playlist",
    get_tracks_from_url: callable | None = None,
    apply_replaygain: bool = False,
) -> None:
    """Download tracks from an external playlist source by searching YouTube Music."""
    if get_tracks_from_url is None:
        raise ValueError("get_tracks_from_url callback is required")

    source_tracks = get_tracks_from_url(url)

    if not source_tracks:
        typer.echo(f"No tracks found in {source_name.lower()} URL.")
        return

    typer.echo(f"Found {len(source_tracks)} tracks in {source_name}")

    downloaded = 0
    skipped = 0
    failed = 0
    downloaded_paths = []

    for i, source_track in enumerate(source_tracks, 1):
        typer.echo(
            f"[{i}/{len(source_tracks)}] Searching: {source_track.artist} - {source_track.name}"
        )

        # Search YouTube Music for this track
        results = search(source_track.search_query, limit=1)

        if not results:
            typer.echo(f"  Not found on YouTube Music, skipping")
            failed += 1
            continue

        yt_track = results[0]
        typer.echo(f"  Found: {yt_track.title} - {yt_track.artist}")

        try:
            audio_path = download(
                video_id=yt_track.video_id,
                output_dir=output_dir,
                audio_format=audio_format,
                filename_template=filename_template,
                fetch_lyrics=fetch_lyrics,
                organization_mode=organization_mode,
                use_primary_artist=use_primary_artist,
                apply_replaygain=apply_replaygain,
            )

            if audio_path:
                downloaded_paths.append(audio_path)
                if "Skipping" not in str(audio_path):
                    downloaded += 1
                else:
                    skipped += 1

        except Exception as e:
            typer.echo(f"  Failed: {e}")
            failed += 1

    typer.echo(f"\nCompleted: {downloaded} downloaded, {skipped} skipped, {failed} failed")

    # Add all downloaded tracks to playlist
    if playlist_name and downloaded_paths:
        from musicload.playlist import add_to_m3u

        add_to_m3u(downloaded_paths, playlist_name, output_dir)
        typer.echo(f"Added {len(downloaded_paths)} track(s) to playlist: {playlist_name}.m3u")


@app.command(name="tag")
def tag(
    directory: Annotated[
        Path, typer.Argument(help="Directory to recursively process", exists=True, file_okay=False)
    ],
    lyrics: Annotated[
        bool,
        typer.Option(
            "--lyrics/--no-lyrics",
            help="Fetch and save lyrics from lrclib.net (default: enabled)",
        ),
    ] = True,
    replaygain: Annotated[
        bool,
        typer.Option(
            "--replaygain/--no-replaygain",
            help="Apply ReplayGain/R128 tags via rsgain (default: enabled)",
        ),
    ] = True,
    metadata: Annotated[
        bool,
        typer.Option(
            "--metadata/--no-metadata",
            help="Enrich missing metadata from YouTube Music (default: enabled)",
        ),
    ] = True,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview what would be done without making changes"),
    ] = False,
):
    """Tag existing audio files with metadata, lyrics, and ReplayGain.

    Recursively processes .opus, .mp3, .flac files in DIRECTORY.

    Examples:

      musicload tag /path/to/music

      musicload tag --no-metadata /path/to/music

      musicload tag --dry-run /path/to/music
    """
    from musicload.tagging import tag_directory

    if dry_run:
        typer.echo("[dry-run] Previewing changes only")

    stats = tag_directory(
        directory,
        do_lyrics=lyrics,
        do_replaygain=replaygain,
        do_metadata=metadata,
        dry_run=dry_run,
    )

    typer.echo(f"\nProcessed {stats.files_found} files:")
    if metadata:
        typer.echo(f"  Metadata: {stats.metadata_enriched} enriched, {stats.metadata_skipped} skipped (complete), {stats.metadata_failed} failed")
    if lyrics:
        typer.echo(f"  Lyrics: {stats.lyrics_added} added, {stats.lyrics_skipped} skipped (already exist), {stats.lyrics_not_found} not found, {stats.lyrics_failed} failed")
    if replaygain:
        typer.echo(f"  ReplayGain: {stats.replaygain_applied} applied, {stats.replaygain_skipped} skipped (already exist), {stats.replaygain_failed} failed")
    if stats.errors:
        typer.echo(f"  Errors: {stats.errors}")


# --- Explore command group ---


@explore_app.command(name="moods")
def explore_moods():
    """List available mood & genre categories."""
    try:
        sections = get_mood_categories()
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if not sections:
        typer.echo("No categories found.")
        return

    for section in sections:
        typer.echo(f"\n{section.title}:")
        for cat in section.categories:
            typer.echo(f"  {cat.title}  (params: {cat.params})")


@explore_app.command(name="mood-playlists")
def explore_mood_playlists_cmd(
    params: Annotated[str, typer.Argument(help="Category identifier from 'explore moods'")],
    do_download: Annotated[
        bool,
        typer.Option(
            "--download",
            "-d",
            help="Download all tracks from all playlists in this category",
        ),
    ] = False,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output directory")
    ] = None,
    audio_format: Annotated[
        AudioFormat | None,
        typer.Option("--format", "-f", help="Audio format (default: opus)"),
    ] = None,
    playlist_name: Annotated[
        str | None,
        typer.Option(
            "--add-to-playlist",
            "-p",
            help="Add downloaded tracks to M3U playlist",
        ),
    ] = None,
    allow_ugc: Annotated[
        bool | None,
        typer.Option(
            "--allow-ugc/--no-allow-ugc",
            help="Include UGC (user-generated content) tracks. Default: exclude",
        ),
    ] = None,
):
    """List playlists for a mood/genre category.

    PARAMS is the category identifier from 'explore moods'.
    Use --download to download all tracks from the playlists.
    """
    if allow_ugc is not None:
        os.environ["MUSICLOAD_ALLOW_UGC"] = "true" if allow_ugc else "false"

    config = get_config()

    try:
        playlists = get_mood_playlists(params)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if not playlists:
        typer.echo("No playlists found.")
        return

    for i, pl in enumerate(playlists, 1):
        author = f" by {pl.author}" if pl.author else ""
        typer.echo(f"{i:2}. {pl.title}{author}")
        typer.echo(f"    Playlist ID: {pl.playlist_id}")

    if do_download:
        for pl in playlists:
            typer.echo(f"\nFetching tracks from: {pl.title}")
            try:
                tracks = get_playlist_tracks(pl.playlist_id, allow_ugc=config.allow_ugc)
            except Exception as e:
                typer.echo(f"  Failed to fetch tracks: {e}")
                continue
            _download_explore_tracks(tracks, output, audio_format, playlist_name)


@explore_app.command(name="charts")
def explore_charts_cmd(
    country: Annotated[
        str,
        typer.Option(
            "--country",
            "-c",
            help="ISO 3166-1 Alpha-2 country code (default: ZZ for global)",
        ),
    ] = "ZZ",
    do_download: Annotated[
        bool,
        typer.Option("--download", "-d", help="Download all chart tracks"),
    ] = False,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output directory")
    ] = None,
    audio_format: Annotated[
        AudioFormat | None,
        typer.Option("--format", "-f", help="Audio format (default: opus)"),
    ] = None,
    playlist_name: Annotated[
        str | None,
        typer.Option(
            "--add-to-playlist",
            "-p",
            help="Add downloaded tracks to M3U playlist",
        ),
    ] = None,
    allow_ugc: Annotated[
        bool | None,
        typer.Option(
            "--allow-ugc/--no-allow-ugc",
            help="Include UGC (user-generated content) tracks. Default: exclude",
        ),
    ] = None,
):
    """Show current music charts.

    Use --download to download all chart tracks.
    """
    if allow_ugc is not None:
        os.environ["MUSICLOAD_ALLOW_UGC"] = "true" if allow_ugc else "false"

    config = get_config()

    try:
        charts = get_charts(country, allow_ugc=config.allow_ugc)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if charts.tracks:
        typer.echo(f"\nTop Songs ({charts.country}):")
        for track in charts.tracks:
            rank = f"#{track.rank} " if track.rank else ""
            typer.echo(f"  {rank}{track.title} - {track.artist}")
            typer.echo(f"    ID: {track.video_id}")

    if charts.artists:
        typer.echo(f"\nTop Artists ({charts.country}):")
        for artist in charts.artists:
            rank = f"#{artist.rank} " if artist.rank else ""
            typer.echo(f"  {rank}{artist.title}")

    if do_download and charts.tracks:
        typer.echo(f"\nDownloading {len(charts.tracks)} chart tracks...")
        config = get_config()
        output_dir = output if output else config.download_dir
        fmt = audio_format.value if audio_format else config.audio_format
        downloaded_paths = []

        for i, track in enumerate(charts.tracks, 1):
            typer.echo(f"[{i}/{len(charts.tracks)}] Downloading: {track.title} - {track.artist}")
            try:
                audio_path = download(
                    video_id=track.video_id,
                    output_dir=output_dir,
                    audio_format=fmt,
                    filename_template=config.filename_template,
                    fetch_lyrics=True,
                    organization_mode=config.organization_mode,
                    use_primary_artist=config.use_primary_artist,
                    apply_replaygain=config.replaygain,
                )
                if audio_path:
                    downloaded_paths.append(audio_path)
            except UnavailableCooldownError as e:
                typer.echo(f"  Skipped (cooldown): {e}")
            except Exception as e:
                typer.echo(f"  Failed: {e}")

        typer.echo(f"\nDownloaded {len(downloaded_paths)} of {len(charts.tracks)} tracks")
        if playlist_name and downloaded_paths:
            from musicload.playlist import add_to_m3u
            add_to_m3u(downloaded_paths, playlist_name, output_dir)
            typer.echo(f"Added {len(downloaded_paths)} track(s) to playlist: {playlist_name}.m3u")


def _download_explore_tracks(
    tracks: list,
    output: Path | None,
    audio_format: AudioFormat | None,
    playlist_name: str | None,
) -> None:
    """Download a list of Track objects from explore results."""
    if not tracks:
        typer.echo("  No tracks to download.")
        return

    config = get_config()
    output_dir = output if output else config.download_dir
    fmt = audio_format.value if audio_format else config.audio_format
    downloaded_paths = []

    for i, track in enumerate(tracks, 1):
        typer.echo(f"[{i}/{len(tracks)}] Downloading: {track.title} - {track.artist}")
        try:
            audio_path = download(
                video_id=track.video_id,
                output_dir=output_dir,
                audio_format=fmt,
                filename_template=config.filename_template,
                fetch_lyrics=True,
                organization_mode=config.organization_mode,
                use_primary_artist=config.use_primary_artist,
                apply_replaygain=config.replaygain,
            )
            if audio_path:
                downloaded_paths.append(audio_path)
        except UnavailableCooldownError as e:
            typer.echo(f"  Skipped (cooldown): {e}")
        except Exception as e:
            typer.echo(f"  Failed: {e}")

    typer.echo(f"\nDownloaded {len(downloaded_paths)} of {len(tracks)} tracks")
    if playlist_name and downloaded_paths:
        from musicload.playlist import add_to_m3u
        add_to_m3u(downloaded_paths, playlist_name, output_dir)
        typer.echo(f"Added {len(downloaded_paths)} track(s) to playlist: {playlist_name}.m3u")


@app.command(name="web")
def web(
    host: Annotated[
        str, typer.Option("--host", help="Host to bind to")
    ] = "0.0.0.0",
    port: Annotated[
        int | None, typer.Option("-p", "--port", help="Port to listen on")
    ] = None,
    cors_origins: Annotated[
        str | None,
        typer.Option(
            "--cors-origins",
            envvar="MUSICLOAD_CORS_ORIGINS",
            help="CORS allowed origins (comma-separated, or '*' for all). Default: *",
        ),
    ] = None,
    web_playlist: Annotated[
        str | None,
        typer.Option(
            "--web-playlist",
            envvar="MUSICLOAD_WEB_PLAYLIST",
            help="M3U playlist name for web downloads (optional)",
        ),
    ] = None,
    organization_mode: Annotated[
        OrganizationMode | None,
        typer.Option(
            "--organization-mode",
            envvar="MUSICLOAD_ORGANIZATION_MODE",
            help="File organization: flat (all in one dir) or album (Artist/Year - Album/Track). Default: flat",
        ),
    ] = None,
    use_primary_artist: Annotated[
        bool | None,
        typer.Option(
            "--use-primary-artist/--no-use-primary-artist",
            help="Use only primary artist for folder names in album mode (strips 'feat.', etc.)",
        ),
    ] = None,
    multi_user: Annotated[
        bool | None,
        typer.Option(
            "--multi-user/--no-multi-user",
            help="Enable per-user M3U playlists via Remote-User header (for reverse proxy SSO). Default: disabled",
        ),
    ] = None,
    replaygain: Annotated[
        bool | None,
        typer.Option(
            "--replaygain/--no-replaygain",
            help="Apply ReplayGain/R128 loudness normalization tags (requires rsgain)",
        ),
    ] = None,
    allow_ugc: Annotated[
        bool | None,
        typer.Option(
            "--allow-ugc/--no-allow-ugc",
            help="Include UGC (user-generated content) tracks in playlist/chart results. Default: exclude",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", envvar="MUSICLOAD_DOWNLOAD_DIR", help="Output directory"),
    ] = None,
):
    """Start the web interface."""
    import uvicorn

    from musicload.config import get_config

    # Override env vars if CLI flags provided
    if cors_origins is not None:
        os.environ["MUSICLOAD_CORS_ORIGINS"] = cors_origins
    if web_playlist is not None:
        os.environ["MUSICLOAD_WEB_PLAYLIST"] = web_playlist
    if organization_mode is not None:
        os.environ["MUSICLOAD_ORGANIZATION_MODE"] = organization_mode.value
    if use_primary_artist is not None:
        os.environ["MUSICLOAD_USE_PRIMARY_ARTIST"] = "true" if use_primary_artist else "false"
    if multi_user is not None:
        os.environ["MUSICLOAD_MULTI_USER"] = "true" if multi_user else "false"
    if replaygain is not None:
        os.environ["MUSICLOAD_REPLAYGAIN"] = "true" if replaygain else "false"
    if allow_ugc is not None:
        os.environ["MUSICLOAD_ALLOW_UGC"] = "true" if allow_ugc else "false"
    if output is not None:
        os.environ["MUSICLOAD_DOWNLOAD_DIR"] = str(output)

    config = get_config()
    server_port = port or config.web_port

    typer.echo(f"Starting web server at http://{host}:{server_port}")

    # Configure musicload loggers to match uvicorn's colored format
    from uvicorn.logging import DefaultFormatter

    musicload_logger = logging.getLogger("musicload")
    musicload_logger.setLevel(logging.INFO)
    musicload_logger.propagate = False
    handler = logging.StreamHandler()
    handler.setFormatter(DefaultFormatter("%(levelprefix)s %(message)s"))
    musicload_logger.addHandler(handler)

    from musicload.web.app import app as web_app

    uvicorn.run(web_app, host=host, port=server_port)


# Entry point for pyproject.toml console_scripts
main = typer.main.get_command(app)

if __name__ == "__main__":
    app()
