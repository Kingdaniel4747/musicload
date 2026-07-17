"""Cron configuration loading and validation."""

import logging
import re
from pathlib import Path

import yaml
from croniter import croniter

from musicload.models.cron import (
    CronConfig,
    ExploreConfig,
    PlaylistConfig,
    PluginInstanceConfig,
)

logger = logging.getLogger(__name__)


def load_config(path: Path) -> CronConfig:
    """
    Load and validate cron configuration from YAML file.

    Args:
        path: Path to cron.yaml file

    Returns:
        Validated CronConfig

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML syntax: {e}")

    if not data:
        raise ValueError("Config file is empty")

    # Check that at least playlists, plugins, or explore are defined
    if "playlists" not in data and "plugins" not in data and "explore" not in data:
        raise ValueError("Config must have 'playlists', 'plugins', or 'explore' key")

    # Load playlists (optional, defaults to empty)
    playlists = {}
    if "playlists" in data:
        playlists_data = data["playlists"]
        if not isinstance(playlists_data, dict):
            raise ValueError("'playlists' must be a dictionary")

        for name, config in playlists_data.items():
            # Validate playlist name
            sanitized_name = validate_playlist_name(name)

            # Validate required fields
            if not isinstance(config, dict):
                raise ValueError(f"Playlist '{name}' config must be a dictionary")

            if "url" not in config:
                raise ValueError(f"Playlist '{name}' missing required field: url")
            if "sync" not in config:
                raise ValueError(f"Playlist '{name}' missing required field: sync")
            if "schedule" not in config:
                raise ValueError(f"Playlist '{name}' missing required field: schedule")

            url = config["url"]
            sync = config["sync"]
            schedule = config["schedule"]

            # Validate types
            if not isinstance(url, str):
                raise ValueError(f"Playlist '{name}' url must be a string")
            if not isinstance(sync, bool):
                raise ValueError(f"Playlist '{name}' sync must be a boolean")
            if not isinstance(schedule, str):
                raise ValueError(f"Playlist '{name}' schedule must be a string")

            # Validate URL
            validate_url(url, name)

            # Validate cron schedule
            validate_cron_schedule(schedule, name)

            playlists[sanitized_name] = PlaylistConfig(
                name=sanitized_name,
                url=url,
                sync=sync,
                schedule=schedule,
            )

    # Load plugins (optional, defaults to empty)
    plugins = {}
    if "plugins" in data:
        plugins_data = data["plugins"]
        if not isinstance(plugins_data, dict):
            raise ValueError("'plugins' must be a dictionary")

        for name, config in plugins_data.items():
            # Validate instance name
            sanitized_name = validate_playlist_name(name)

            # Validate required fields
            if not isinstance(config, dict):
                raise ValueError(f"Plugin '{name}' config must be a dictionary")

            if "type" not in config:
                raise ValueError(f"Plugin '{name}' missing required field: type")
            if "sync" not in config:
                raise ValueError(f"Plugin '{name}' missing required field: sync")
            if "schedule" not in config:
                raise ValueError(f"Plugin '{name}' missing required field: schedule")
            if "config" not in config:
                raise ValueError(f"Plugin '{name}' missing required field: config")

            plugin_type = config["type"]

            # Validate types
            if not isinstance(plugin_type, str):
                raise ValueError(f"Plugin '{name}' type must be a string")
            if not isinstance(config["sync"], bool):
                raise ValueError(f"Plugin '{name}' sync must be a boolean")
            if not isinstance(config["schedule"], str):
                raise ValueError(f"Plugin '{name}' schedule must be a string")
            if not isinstance(config["config"], dict):
                raise ValueError(f"Plugin '{name}' config must be a dictionary")

            # Get plugin class and validate config
            try:
                from musicload.plugins.registry import get_plugin

                plugin_class = get_plugin(plugin_type)
                plugin_instance = plugin_class()
                plugin_instance.validate_config(config["config"])
            except Exception as e:
                raise ValueError(f"Plugin '{name}' configuration invalid: {e}")

            # Validate cron schedule
            validate_cron_schedule(config["schedule"], name)

            plugins[sanitized_name] = PluginInstanceConfig(
                name=sanitized_name,
                type=plugin_type,
                sync=config["sync"],
                schedule=config["schedule"],
                config=config["config"],
            )

    # Load explore entries (optional, defaults to empty)
    explore = {}
    if "explore" in data and data["explore"] is not None:
        explore_data = data["explore"]
        if not isinstance(explore_data, dict):
            raise ValueError("'explore' must be a dictionary")

        for name, config in explore_data.items():
            sanitized_name = validate_playlist_name(name)

            if not isinstance(config, dict):
                raise ValueError(f"Explore '{name}' config must be a dictionary")

            if "type" not in config:
                raise ValueError(f"Explore '{name}' missing required field: type")
            if "sync" not in config:
                raise ValueError(f"Explore '{name}' missing required field: sync")
            if "schedule" not in config:
                raise ValueError(f"Explore '{name}' missing required field: schedule")

            explore_type = config["type"]
            if explore_type not in ("charts", "mood", "new_releases"):
                raise ValueError(
                    f"Explore '{name}' type must be 'charts', 'mood', or 'new_releases', got '{explore_type}'"
                )

            if not isinstance(config["sync"], bool):
                raise ValueError(f"Explore '{name}' sync must be a boolean")
            if not isinstance(config["schedule"], str):
                raise ValueError(f"Explore '{name}' schedule must be a string")

            # Type-specific validation
            country = "ZZ"
            params = ""
            playlist_id = ""

            if explore_type == "charts":
                country = config.get("country", "ZZ")
                if not isinstance(country, str):
                    raise ValueError(f"Explore '{name}' country must be a string")
                validate_country_code(country, name)

            elif explore_type == "mood":
                if "params" not in config:
                    raise ValueError(f"Explore '{name}' (type=mood) missing required field: params")
                params = config["params"]
                if not isinstance(params, str) or not params.strip():
                    raise ValueError(f"Explore '{name}' params must be a non-empty string")

                # Optional playlist_id for targeting a specific playlist
                playlist_id = config.get("playlist_id", "")
                if playlist_id and not isinstance(playlist_id, str):
                    raise ValueError(f"Explore '{name}' playlist_id must be a string")

            validate_cron_schedule(config["schedule"], name)

            # Parse optional limit (default: 0 = no limit)
            limit = config.get("limit", 0)
            if not isinstance(limit, int) or isinstance(limit, bool):
                raise ValueError(f"Explore '{name}' limit must be an integer")
            if limit < 0:
                raise ValueError(f"Explore '{name}' limit must be >= 0 (0 = no limit)")

            explore[sanitized_name] = ExploreConfig(
                name=sanitized_name,
                type=explore_type,
                sync=config["sync"],
                schedule=config["schedule"],
                country=country,
                params=params,
                playlist_id=playlist_id,
                limit=limit,
            )

    logger.info(
        "Loaded configuration for %d playlist(s), %d plugin(s), and %d explore source(s)",
        len(playlists),
        len(plugins),
        len(explore),
    )
    return CronConfig(playlists=playlists, plugins=plugins, explore=explore)


def validate_playlist_name(name: str) -> str:
    """
    Validate and sanitize playlist name.

    Only allows alphanumeric characters, dash, and underscore to prevent
    path traversal and filesystem issues.

    Args:
        name: Playlist name

    Returns:
        Sanitized playlist name

    Raises:
        ValueError: If name contains invalid characters
    """
    if not re.match(r"^[\w\-]+$", name):
        raise ValueError(
            f"Invalid playlist name '{name}': "
            "only alphanumeric, dash, and underscore allowed"
        )
    return name


def validate_url(url: str, playlist_name: str) -> None:
    """
    Validate playlist URL.

    Ensures URL is a valid YouTube, YouTube Music, or Deezer URL.

    Args:
        url: Playlist URL
        playlist_name: Playlist name for error messages

    Raises:
        ValueError: If URL is invalid
    """
    # YouTube/YouTube Music patterns
    youtube_patterns = [
        r"^https?://(www\.)?youtube\.com/",
        r"^https?://music\.youtube\.com/",
        r"^https?://youtu\.be/",
    ]

    # Deezer patterns
    deezer_patterns = [
        r"^https?://(www\.)?deezer\.com/(?:[a-z]{2}/)?playlist/",
    ]

    is_valid = False
    for pattern in youtube_patterns + deezer_patterns:
        if re.match(pattern, url):
            is_valid = True
            break

    if not is_valid:
        raise ValueError(
            f"Playlist '{playlist_name}' has invalid URL: {url}. "
            "Must be a YouTube, YouTube Music, or Deezer URL"
        )


def validate_cron_schedule(schedule: str, playlist_name: str) -> None:
    """
    Validate cron schedule expression.

    Args:
        schedule: Cron expression (e.g., "5 4 * * *")
        playlist_name: Playlist name for error messages

    Raises:
        ValueError: If cron expression is invalid
    """
    if not schedule or not schedule.strip():
        raise ValueError(f"Playlist '{playlist_name}' has empty schedule")

    try:
        # croniter will raise ValueError if expression is invalid
        croniter(schedule)
    except Exception as e:
        raise ValueError(
            f"Playlist '{playlist_name}' has invalid cron schedule '{schedule}': {e}"
        )


def validate_country_code(country: str, explore_name: str) -> None:
    """
    Validate ISO 3166-1 Alpha-2 country code.

    Allows standard 2-letter codes plus 'ZZ' for global charts.

    Args:
        country: Country code string
        explore_name: Explore entry name for error messages

    Raises:
        ValueError: If country code is invalid
    """
    if not re.match(r"^[A-Z]{2}$", country):
        raise ValueError(
            f"Explore '{explore_name}' has invalid country code '{country}': "
            "must be a 2-letter uppercase ISO 3166-1 Alpha-2 code (e.g., 'US', 'GB', 'ZZ')"
        )
