"""Explore-related command-line commands."""

import os
from pathlib import Path
from typing import Annotated

import typer

from musicload.cli_types import AudioFormat
from musicload.config import get_config
from musicload.download import UnavailableCooldownError, download
from musicload.explore import get_charts, get_mood_categories, get_mood_playlists
from musicload.search import get_playlist_tracks

explore_app = typer.Typer(help="Explore moods, genres, and charts on YouTube Music.")


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
    params: Annotated[
        str, typer.Argument(help="Category identifier from 'explore moods'")
    ],
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
        _download_explore_tracks(charts.tracks, output, audio_format, playlist_name)


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
        typer.echo(
            f"Added {len(downloaded_paths)} track(s) to playlist: {playlist_name}.m3u"
        )
