"""Base plugin protocol and data structures for musicload plugins."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class Song:
    """Represents a song to be downloaded.

    This is the core data structure that all plugins must return.
    """

    artist: str
    title: str
    album: str | None = None
    duration_seconds: int | None = None
    artists: list[str] | None = None

    @property
    def search_query(self) -> str:
        """Generate search query for YouTube Music."""
        if self.album:
            return f"{self.artist} - {self.title} {self.album}"
        return f"{self.artist} - {self.title}"

    @property
    def cache_key(self) -> str:
        """Generate unique cache key for deduplication."""
        return f"{self.artist}::{self.title}"


@dataclass
class PluginConfig:
    """Base configuration that all plugins receive."""

    name: str  # Plugin instance name (from YAML)
    download_dir: Path
    audio_format: str
    filename_template: str
    config: dict  # Plugin-specific config
    organization_mode: str = "flat"  # File organization mode ("flat" or "album")
    use_primary_artist: bool = False  # Extract primary artist for folder


@dataclass
class SyncResult:
    """Result of a plugin sync operation."""

    plugin_name: str
    downloaded: int
    skipped: int
    failed: int
    errors: list[str]


@runtime_checkable
class Plugin(Protocol):
    """Protocol that all plugins must implement.

    This uses Protocol (structural subtyping) instead of ABC to allow
    maximum flexibility for third-party plugins.
    """

    @property
    def name(self) -> str:
        """Plugin type name (e.g., 'listenbrainz', 'rss')."""
        ...

    @property
    def config_schema(self) -> dict:
        """JSON schema for plugin configuration validation.

        Returns a dict describing required/optional fields.
        Example:
        {
            "required": ["url"],
            "optional": {
                "user_agent": "Mozilla/5.0...",
                "timeout": 10
            }
        }
        """
        ...

    def fetch_songs(self, config: PluginConfig) -> list[Song]:
        """Fetch songs from the plugin source.

        Args:
            config: Plugin configuration

        Returns:
            List of Song objects to download

        Raises:
            PluginError: If fetching fails
        """
        ...

    def validate_config(self, config: dict) -> None:
        """Validate plugin-specific configuration.

        Args:
            config: Plugin configuration dict

        Raises:
            ValueError: If configuration is invalid
        """
        ...


class PluginError(Exception):
    """Base exception for plugin errors."""

    pass
