"""Search domain models — YouTube Music tracks, albums, charts, moods, metadata."""

from pydantic import BaseModel, ConfigDict, computed_field


class Track(BaseModel):
    """Represents a music track from YouTube Music."""

    model_config = ConfigDict(frozen=True)

    video_id: str
    title: str
    artist: str
    artists: list[str]
    album: str | None
    duration_seconds: int
    thumbnail_url: str | None
    view_count: str | None
    video_type: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_display(self) -> str:
        """Format duration as MM:SS."""
        minutes = self.duration_seconds // 60
        seconds = self.duration_seconds % 60
        return f"{minutes}:{seconds:02d}"


class Album(BaseModel):
    """Represents an album from YouTube Music."""

    model_config = ConfigDict(frozen=True)

    browse_id: str
    title: str
    artist: str
    year: int | None
    track_count: int | None
    thumbnail_url: str | None
    audio_playlist_id: str | None = None
    album_type: str | None = None
    is_explicit: bool = False


class MoodCategory(BaseModel):
    """A single mood/genre category with a params identifier for fetching playlists."""

    model_config = ConfigDict(frozen=True)

    title: str
    params: str


class MoodSection(BaseModel):
    """A section of mood/genre categories (e.g., 'Genres', 'Moods & moments')."""

    model_config = ConfigDict(frozen=True)

    title: str
    categories: list[MoodCategory] = []


class MoodPlaylist(BaseModel):
    """A playlist from a mood/genre category."""

    model_config = ConfigDict(frozen=True)

    playlist_id: str
    title: str
    thumbnail_url: str | None
    author: str | None


class ChartTrack(BaseModel):
    """A track from the music charts."""

    model_config = ConfigDict(frozen=True)

    video_id: str
    title: str
    artist: str
    artists: list[str]
    album: str | None
    thumbnail_url: str | None
    rank: str | None
    trend: str | None
    view_count: str | None = None
    duration_seconds: int = 0
    video_type: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_display(self) -> str:
        """Format duration as MM:SS."""
        minutes = self.duration_seconds // 60
        seconds = self.duration_seconds % 60
        return f"{minutes}:{seconds:02d}"


class ChartArtist(BaseModel):
    """An artist from the music charts."""

    model_config = ConfigDict(frozen=True)

    browse_id: str
    title: str
    thumbnail_url: str | None
    rank: str | None
    trend: str | None


class Charts(BaseModel):
    """Chart data for a country."""

    model_config = ConfigDict(frozen=True)

    country: str
    tracks: list[ChartTrack] = []
    artists: list[ChartArtist] = []


class SongMetadata(BaseModel):
    """Clean metadata for a song, fetched from YouTube Music API.

    Used for lyrics lookup where accurate title/artist/album/duration
    are critical for matching against lrclib.net.
    """

    model_config = ConfigDict(frozen=True)

    title: str
    artist: str
    album: str | None
    duration_seconds: int
