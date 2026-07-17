"""Playlist and plugin state models for JSON persistence."""

from pydantic import BaseModel


class TrackState(BaseModel):
    """State for a single track."""

    video_id: str
    title: str
    artist: str
    file_path: str
    downloaded_at: str


class PlaylistState(BaseModel):
    """State for an entire playlist."""

    playlist_name: str
    url: str
    last_check: str
    tracks: list[TrackState]


class PluginTrackState(BaseModel):
    """State for a track downloaded by a plugin."""

    cache_key: str
    artist: str
    title: str
    file_path: str
    downloaded_at: str
    video_id: str | None = None


class PluginState(BaseModel):
    """State for a plugin sync instance."""

    plugin_name: str
    plugin_type: str
    source_url: str | None
    last_check: str
    tracks: list[PluginTrackState]
