"""Cron configuration models."""

from pydantic import BaseModel, ConfigDict, field_validator


class PlaylistConfig(BaseModel):
    """Configuration for a single playlist."""

    model_config = ConfigDict(frozen=True)

    name: str
    url: str
    sync: bool
    schedule: str


class PluginInstanceConfig(BaseModel):
    """Configuration for a single plugin instance."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str  # Plugin type name
    sync: bool
    schedule: str
    config: dict  # Plugin-specific config


class ExploreConfig(BaseModel):
    """Configuration for a single explore sync entry (charts or mood).

    type: "charts" or "mood"
    country: ISO 3166-1 Alpha-2 code (only for type="charts", default "ZZ")
    params: Mood/genre params string (only for type="mood")
    playlist_id: Specific playlist ID to sync (only for type="mood", optional)
    sync: If True, delete tracks removed from the source
    schedule: Cron expression
    name: Internal name for state/playlist files
    limit: Maximum number of tracks to sync (0 = no limit)
    """

    model_config = ConfigDict(frozen=True)

    name: str
    type: str  # "charts" or "mood"
    sync: bool
    schedule: str
    country: str = "ZZ"
    params: str = ""
    playlist_id: str = ""
    limit: int = 0

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("charts", "mood", "new_releases"):
            raise ValueError(f"type must be 'charts', 'mood', or 'new_releases', got '{v}'")
        return v

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, v: int) -> int:
        if v < 0:
            raise ValueError("limit must be >= 0 (0 = no limit)")
        return v


class CronConfig(BaseModel):
    """Root configuration for cron playlists, plugins, and explore entries."""

    playlists: dict[str, PlaylistConfig]
    plugins: dict[str, PluginInstanceConfig]
    explore: dict[str, ExploreConfig] = {}
