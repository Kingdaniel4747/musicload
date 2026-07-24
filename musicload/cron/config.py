"""Load the minimal Musicload cron configuration."""

import logging
import os
import re
from pathlib import Path

import yaml
from croniter import croniter

from musicload.models.cron import CronConfig, PlaylistConfig, PluginInstanceConfig

logger = logging.getLogger(__name__)

_SUPPORTED_SECTIONS = {"playlists", "plugins"}


def get_cron_config_path() -> Path:
    """Return the shared cron config path used by web and worker."""
    configured = os.environ.get("MUSICLOAD_CRON_CONFIG")
    if configured:
        return Path(configured)

    from musicload.config import get_config

    return get_config().data_dir / "cron.yaml"


def load_config(path: Path) -> CronConfig:
    """Load YouTube playlist and ListenBrainz jobs from a YAML file."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as config_file:
            data = yaml.safe_load(config_file)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML syntax: {exc}") from exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("Config file must contain 'playlists' and/or 'plugins'")

    unsupported = set(data) - _SUPPORTED_SECTIONS
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(
            f"Unsupported cron section(s): {names}. "
            "Musicload cron supports only YouTube playlists and ListenBrainz."
        )

    playlists = _load_playlists(data.get("playlists", {}))
    plugins = _load_listenbrainz_jobs(data.get("plugins", {}))

    logger.info(
        "Loaded %d YouTube playlist(s) and %d ListenBrainz job(s)",
        len(playlists),
        len(plugins),
    )
    return CronConfig(playlists=playlists, plugins=plugins)


def load_config_document(path: Path) -> dict:
    """Load a config document for editing, treating a missing file as empty."""
    if not path.exists():
        return {"playlists": {}, "plugins": {}}
    config = load_config(path)
    return {
        "playlists": {
            name: {
                "url": job.url,
                "sync": job.sync,
                "schedule": job.schedule,
            }
            for name, job in config.playlists.items()
        },
        "plugins": {
            name: {
                "type": job.type,
                "sync": job.sync,
                "schedule": job.schedule,
                "config": job.config,
            }
            for name, job in config.plugins.items()
        },
    }


def save_config_document(path: Path, data: dict) -> None:
    """Validate and atomically save a cron configuration document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as config_file:
            yaml.safe_dump(
                data,
                config_file,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            )
        load_config(temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_playlists(data: object) -> dict[str, PlaylistConfig]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("'playlists' must be a dictionary")

    playlists: dict[str, PlaylistConfig] = {}
    for name, raw_config in data.items():
        safe_name = validate_job_name(name)
        if not isinstance(raw_config, dict):
            raise ValueError(f"Playlist '{name}' config must be a dictionary")

        url = raw_config.get("url")
        schedule = raw_config.get("schedule")
        sync = raw_config.get("sync", False)

        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"Playlist '{name}' requires a URL")
        if not isinstance(schedule, str):
            raise ValueError(f"Playlist '{name}' requires a cron schedule")
        if not isinstance(sync, bool):
            raise ValueError(f"Playlist '{name}' sync must be a boolean")

        validate_youtube_url(url, name)
        validate_cron_schedule(schedule, name)
        playlists[safe_name] = PlaylistConfig(
            name=safe_name,
            url=url,
            sync=sync,
            schedule=schedule,
        )

    return playlists


def _load_listenbrainz_jobs(data: object) -> dict[str, PluginInstanceConfig]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("'plugins' must be a dictionary")

    jobs: dict[str, PluginInstanceConfig] = {}
    for name, raw_config in data.items():
        safe_name = validate_job_name(name)
        if not isinstance(raw_config, dict):
            raise ValueError(f"ListenBrainz job '{name}' config must be a dictionary")

        plugin_type = raw_config.get("type")
        schedule = raw_config.get("schedule")
        sync = raw_config.get("sync", False)
        plugin_config = raw_config.get("config")

        if plugin_type != "listenbrainz":
            raise ValueError(
                f"Cron source '{name}' has unsupported type '{plugin_type}'. "
                "Only 'listenbrainz' is supported."
            )
        if not isinstance(schedule, str):
            raise ValueError(f"ListenBrainz job '{name}' requires a cron schedule")
        if not isinstance(sync, bool):
            raise ValueError(f"ListenBrainz job '{name}' sync must be a boolean")
        if not isinstance(plugin_config, dict):
            raise ValueError(f"ListenBrainz job '{name}' requires a config dictionary")

        from musicload.plugins.listenbrainz import ListenbrainzPlugin

        ListenbrainzPlugin().validate_config(plugin_config)
        validate_cron_schedule(schedule, name)
        jobs[safe_name] = PluginInstanceConfig(
            name=safe_name,
            type="listenbrainz",
            sync=sync,
            schedule=schedule,
            config=plugin_config,
        )

    return jobs


def validate_job_name(name: object) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[\w-]+", name):
        raise ValueError(
            f"Invalid cron job name '{name}': use only letters, numbers, dashes, and underscores"
        )
    return name


def validate_youtube_url(url: str, name: str) -> None:
    patterns = (
        r"^https?://(www\.)?youtube\.com/",
        r"^https?://music\.youtube\.com/",
        r"^https?://youtu\.be/",
    )
    if not any(re.match(pattern, url) for pattern in patterns):
        raise ValueError(
            f"Playlist '{name}' has an invalid URL. "
            "Only YouTube and YouTube Music URLs are supported by cron."
        )


def validate_cron_schedule(schedule: str, name: str) -> None:
    if not schedule.strip():
        raise ValueError(f"Cron job '{name}' has an empty schedule")
    try:
        croniter(schedule)
    except Exception as exc:
        raise ValueError(
            f"Cron job '{name}' has an invalid schedule '{schedule}': {exc}"
        ) from exc
