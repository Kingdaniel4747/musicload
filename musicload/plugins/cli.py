"""CLI command for plugin-based sync."""

import json
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from musicload.config import get_config
from musicload.plugins.base import PluginConfig
from musicload.plugins.registry import discover_plugins, get_plugin, list_plugins
from musicload.plugins.sync import sync_plugin_instance

logger = logging.getLogger(__name__)


class AudioFormat(str, Enum):
    opus = "opus"
    mp3 = "mp3"
    flac = "flac"


class OrganizationMode(str, Enum):
    flat = "flat"
    album = "album"


plugins_app = typer.Typer(help="Run plugin-based sync operations.")


@plugins_app.callback()
def plugins_callback():
    """Run plugin-based sync operations."""
    discover_plugins()


@plugins_app.command(name="list")
def list_available_plugins():
    """List all available plugins."""
    discover_plugins()
    plugin_names = list_plugins()

    if not plugin_names:
        typer.echo("No plugins available.")
        return

    typer.echo("Available plugins:\n")
    for plugin_name in plugin_names:
        plugin_class = get_plugin(plugin_name)
        plugin = plugin_class()

        typer.echo(f"  {plugin_name}")
        schema = plugin.config_schema
        if schema.get("required"):
            typer.echo(f"    Required: {', '.join(schema['required'])}")
        if schema.get("optional"):
            typer.echo(f"    Optional: {', '.join(schema['optional'].keys())}")
        typer.echo()


@plugins_app.command(name="run")
def sync_once(
    plugin_name: Annotated[str, typer.Argument(help="Plugin name to run")],
    config: Annotated[
        str,
        typer.Option("--config", "-c", help="Plugin config as JSON string"),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Download directory"),
    ] = None,
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
    replaygain: Annotated[
        bool | None,
        typer.Option(
            "--replaygain/--no-replaygain",
            help="Apply ReplayGain/R128 loudness normalization tags (requires rsgain)",
        ),
    ] = None,
):
    """Run a plugin sync once (without cron.yaml).

    Examples:

      musicload plugins run listenbrainz --config '{"user": "myuser"}'

      musicload plugins run rss --config '{"url": "https://..."}'
    """
    discover_plugins()

    if replaygain is not None:
        os.environ["MUSICLOAD_REPLAYGAIN"] = "true" if replaygain else "false"

    main_config = get_config()
    download_dir = output if output else main_config.download_dir

    # Use CLI parameters if provided, otherwise use config defaults
    fmt = audio_format.value if audio_format is not None else main_config.audio_format
    org_mode = organization_mode.value if organization_mode is not None else main_config.organization_mode
    primary_artist = use_primary_artist if use_primary_artist is not None else main_config.use_primary_artist

    try:
        # Parse config
        plugin_config = json.loads(config)

        # Get plugin
        plugin_class = get_plugin(plugin_name)
        plugin = plugin_class()

        # Validate config
        plugin.validate_config(plugin_config)

        # Create config object
        cfg = PluginConfig(
            name=plugin_name,
            download_dir=download_dir,
            audio_format=fmt,
            filename_template=main_config.filename_template,
            config=plugin_config,
            organization_mode=org_mode,
            use_primary_artist=primary_artist,
        )

        # Run sync
        typer.echo(f"Running {plugin_name} sync...")
        result = sync_plugin_instance(plugin, cfg, sync_mode=False)

        typer.echo(
            f"\nCompleted: {result.downloaded} downloaded, "
            f"{result.skipped} skipped, {result.failed} failed"
        )

        if result.errors:
            typer.echo("\nErrors:")
            for error in result.errors:
                typer.echo(f"  - {error}")

    except json.JSONDecodeError as e:
        typer.echo(f"Error: Invalid JSON config: {e}", err=True)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
