"""Cron configuration models for YouTube playlists and ListenBrainz."""

from pydantic import BaseModel, ConfigDict, Field


class PlaylistConfig(BaseModel):
    """Configuration for a subscribed YouTube playlist."""

    model_config = ConfigDict(frozen=True)

    name: str
    url: str
    sync: bool = False
    schedule: str


class PluginInstanceConfig(BaseModel):
    """Configuration for a ListenBrainz recommendation job."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    sync: bool = False
    schedule: str
    config: dict


class CronConfig(BaseModel):
    """Root configuration for the two supported cron sources."""

    playlists: dict[str, PlaylistConfig] = Field(default_factory=dict)
    plugins: dict[str, PluginInstanceConfig] = Field(default_factory=dict)
