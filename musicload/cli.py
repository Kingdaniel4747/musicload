"""Command-line interface for Musicload."""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from musicload.cli_explore import explore_app
from musicload.cli_types import AudioFormat, CookieMode, OrganizationMode
from musicload.config import get_config
from musicload.download import UnavailableCooldownError, download, download_url
from musicload.search import search

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)


app = typer.Typer(help="Musicload - Search and download music from YouTube Music.")
app.add_typer(explore_app, name="explore")

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
            help="Data directory for state, cookies, logs, cache, and metadata files. Default: ~/.musicload",
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


@dataclass(frozen=True)
class _CliDownloadOptions:
    output_dir: Path
    audio_format: str
    filename_template: str
    fetch_lyrics: bool
    playlist_name: str | None
    organization_mode: str
    use_primary_artist: bool
    apply_replaygain: bool


def _add_downloads_to_playlist(
    paths: list[Path], options: _CliDownloadOptions, message: str
) -> None:
    if not options.playlist_name or not paths:
        return
    from musicload.playlist import add_to_m3u

    add_to_m3u(paths, options.playlist_name, options.output_dir)
    typer.echo(message)


def _download_query_input(query: str, options: _CliDownloadOptions) -> None:
    results = search(query, limit=1)
    if not results:
        typer.echo(f"Error: No results found for: {query}", err=True)
        raise typer.Exit(code=1)

    track = results[0]
    typer.echo(f"Found: {track.title} - {track.artist}")
    audio_path = download(
        video_id=track.video_id,
        output_dir=options.output_dir,
        audio_format=options.audio_format,
        filename_template=options.filename_template,
        fetch_lyrics=options.fetch_lyrics,
        organization_mode=options.organization_mode,
        use_primary_artist=options.use_primary_artist,
        apply_replaygain=options.apply_replaygain,
    )
    if not audio_path:
        return
    typer.echo(f"Downloaded: {audio_path}")
    _add_downloads_to_playlist(
        [audio_path],
        options,
        f"Added to playlist: {options.playlist_name}.m3u",
    )


def _download_youtube_url(url: str, options: _CliDownloadOptions) -> None:
    result = download_url(
        url=url,
        output_dir=options.output_dir,
        audio_format=options.audio_format,
        filename_template=options.filename_template,
        fetch_lyrics=options.fetch_lyrics,
        organization_mode=options.organization_mode,
        use_primary_artist=options.use_primary_artist,
        apply_replaygain=options.apply_replaygain,
    )
    if isinstance(result, list):
        typer.echo(f"Downloaded {len(result)} tracks to {options.output_dir}")
        _add_downloads_to_playlist(
            result,
            options,
            f"Added {len(result)} track(s) to playlist: {options.playlist_name}.m3u",
        )
    elif result:
        typer.echo(f"Downloaded: {result}")
        _add_downloads_to_playlist(
            [result],
            options,
            f"Added to playlist: {options.playlist_name}.m3u",
        )
    else:
        typer.echo("Download completed but could not locate file.")


def _download_url_input(url: str, options: _CliDownloadOptions) -> None:
    from musicload.deezer import get_tracks_from_url as get_deezer_tracks_from_url
    from musicload.deezer import is_deezer_url

    if not is_deezer_url(url):
        _download_youtube_url(url, options)
        return
    _download_external_url(
        url=url,
        output_dir=options.output_dir,
        audio_format=options.audio_format,
        filename_template=options.filename_template,
        fetch_lyrics=options.fetch_lyrics,
        playlist_name=options.playlist_name,
        organization_mode=options.organization_mode,
        use_primary_artist=options.use_primary_artist,
        source_name="Deezer playlist",
        get_tracks_from_url=get_deezer_tracks_from_url,
        apply_replaygain=options.apply_replaygain,
    )


def _download_video_input(video_id: str, options: _CliDownloadOptions) -> None:
    audio_path = download(
        video_id=video_id,
        output_dir=options.output_dir,
        audio_format=options.audio_format,
        filename_template=options.filename_template,
        fetch_lyrics=options.fetch_lyrics,
        organization_mode=options.organization_mode,
        use_primary_artist=options.use_primary_artist,
        apply_replaygain=options.apply_replaygain,
    )
    if not audio_path:
        typer.echo("Download completed but could not locate file.")
        return
    typer.echo(f"Downloaded: {audio_path}")
    _add_downloads_to_playlist(
        [audio_path],
        options,
        f"Added to playlist: {options.playlist_name}.m3u",
    )


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
            help="File organization: flat (all in one dir) or album (Artist/Year - Album/Track). Default: album",
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
    options = _CliDownloadOptions(
        output_dir=output or config.download_dir,
        audio_format=audio_format.value if audio_format else config.audio_format,
        filename_template=filename_template or config.filename_template,
        fetch_lyrics=not no_lyrics,
        playlist_name=playlist_name,
        organization_mode=(
            organization_mode.value
            if organization_mode
            else config.organization_mode
        ),
        use_primary_artist=(
            use_primary_artist
            if use_primary_artist is not None
            else config.use_primary_artist
        ),
        apply_replaygain=config.replaygain,
    )

    try:
        if query:
            _download_query_input(query, options)
        elif url:
            _download_url_input(url, options)
        else:
            _download_video_input(video_id, options)
    except UnavailableCooldownError as error:
        typer.echo(str(error))
    except typer.Exit:
        raise
    except Exception as error:
        typer.echo(f"Error: {error}", err=True)
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
    get_tracks_from_url: Callable | None = None,
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
            typer.echo("  Not found on YouTube Music, skipping")
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
            help="File organization: flat (all in one dir) or album (Artist/Year - Album/Track). Default: album",
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

    from musicload.web.logs import configure_process_file_logging

    configure_process_file_logging(config.data_dir / "logs" / "web.log")
    logging.getLogger(__name__).info(
        "Starting web server at http://%s:%s", host, server_port
    )

    from musicload.web.app import app as web_app

    uvicorn.run(web_app, host=host, port=server_port, log_config=None)


# Entry point for pyproject.toml console_scripts
main = typer.main.get_command(app)

if __name__ == "__main__":
    app()
