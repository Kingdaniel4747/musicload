"""Cron CLI command implementation."""

import logging
import os
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from musicload.config import get_config
from musicload.cron.scheduler import CronScheduler

logger = logging.getLogger(__name__)


class AudioFormat(str, Enum):
    opus = "opus"
    mp3 = "mp3"
    flac = "flac"


class OrganizationMode(str, Enum):
    flat = "flat"
    album = "album"


def cron_command(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to cron configuration file (default: cron.yaml)",
        ),
    ] = Path("cron.yaml"),
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Override download directory"),
    ] = None,
    once: Annotated[
        bool,
        typer.Option("--once", help="Run all sync jobs once and exit (skip scheduling)"),
    ] = False,
    audio_format: Annotated[
        AudioFormat | None,
        typer.Option(
            "--format",
            "-f",
            help="Audio format for downloads. Default: opus",
            envvar="MUSICLOAD_AUDIO_FORMAT",
        ),
    ] = None,
    organization_mode: Annotated[
        OrganizationMode | None,
        typer.Option(
            "--organization-mode",
            help="File organization: flat (all in one dir) or album (Artist/Year - Album/Track). Default: flat",
            envvar="MUSICLOAD_ORGANIZATION_MODE",
        ),
    ] = None,
    use_primary_artist: Annotated[
        bool | None,
        typer.Option(
            "--use-primary-artist/--no-use-primary-artist",
            help="Use only primary artist for folder names in album mode",
        ),
    ] = None,
):
    """
    Run continuous sync based on cron.yaml.

    Monitors YouTube playlists and ListenBrainz recommendations and downloads
    new tracks according to the cron schedule defined for each entry.

    Configuration file format (cron.yaml):

    \b
    playlists:
      playlist_name:
        url: <YouTube or YouTube Music playlist URL>
        sync: <true to delete removed tracks, false to keep them>
        schedule: <cron expression, e.g., "5 4 * * *">

    \b
    plugins:
      listenbrainz-weekly:
        type: listenbrainz
        sync: false
        schedule: "0 8 * * 1"
        config:
          user: <ListenBrainz username>
          recommendation_type: weekly-exploration

    Examples:

    \b
      # Run continuously with default config
      musicload cron

    \b
      # Run with custom config file
      musicload cron --config /path/to/cron.yaml

    \b
      # Run all sync jobs once and exit
      musicload cron --once

    \b
      # Override download directory
      musicload cron --output /custom/downloads
    """
    # Set environment variables from CLI flags (they override env vars)
    if audio_format is not None:
        os.environ["MUSICLOAD_AUDIO_FORMAT"] = audio_format.value
    if organization_mode is not None:
        os.environ["MUSICLOAD_ORGANIZATION_MODE"] = organization_mode.value
    if use_primary_artist is not None:
        os.environ["MUSICLOAD_USE_PRIMARY_ARTIST"] = "true" if use_primary_artist else "false"

    config_path = Path(config)
    download_dir = output

    # If download_dir not specified, use config default
    if not download_dir:
        main_config = get_config()
        download_dir = main_config.download_dir

    try:
        scheduler = CronScheduler(config_path, download_dir)

        if once:
            # Run all sync jobs once and exit
            typer.echo("Running all sync jobs once...")
            scheduler.sync_all_once()
            typer.echo("Done.")
        else:
            # Run continuously
            typer.echo(f"Starting cron scheduler with config: {config_path}")
            typer.echo(f"Download directory: {download_dir}")
            scheduler.run_forever()

    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except ValueError as e:
        typer.echo(f"Error: Configuration error: {e}", err=True)
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        typer.echo("\nStopping...")
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: Unexpected error: {e}", err=True)
        raise typer.Exit(code=1)
