"""Pydantic request and response schemas for the web API."""

from pydantic import BaseModel, Field


class DownloadRequest(BaseModel):
    """Request body for download endpoint."""

    video_id: str
    title: str
    artist: str
    artists: list[str] | None = None
    audio_format: str = "opus"


class LoginRequest(BaseModel):
    """Navidrome credentials used only for the current login attempt."""

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class ListenBrainzSettingsRequest(BaseModel):
    """ListenBrainz identity belonging to the current Musicload account."""

    username: str = Field(min_length=1, max_length=128)
    auto_download: bool = False
    download_weekday: int = Field(default=0, ge=0, le=6)
    download_time: str = Field(default="03:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="UTC", min_length=1, max_length=64)


class AppSettingsRequest(BaseModel):
    """Web-managed application settings."""

    audio_format: str = "opus"
    filename_template: str = Field(min_length=1, max_length=512)
    organization_mode: str = "album"
    use_primary_artist: bool = False
    web_playlist_name: str | None = Field(default=None, max_length=128)
    gotify_url: str | None = Field(default=None, max_length=2048)
    gotify_token: str | None = Field(default=None, max_length=1024)
    clear_gotify_token: bool = False
    cookie_mode: str = "auto"
    multi_user: bool = False
    allow_ugc: bool = False
    navidrome_url: str | None = Field(default=None, max_length=2048)
    session_secret: str | None = Field(default=None, max_length=1024)
    clear_session_secret: bool = False
    session_https_only: bool = True
    listenbrainz_web: bool = False


class DownloadResponse(BaseModel):
    """Response body for download endpoint."""

    success: bool
    message: str
    file_path: str | None = None
    file_name: str | None = None


class TrackResponse(BaseModel):
    """Track data for API responses."""

    video_id: str
    title: str
    artist: str
    artists: list[str]
    album: str | None
    duration: str
    thumbnail_url: str | None
    view_count: str | None
    video_type: str | None = None


def track_to_response(track) -> TrackResponse:
    """Convert a Track-like object into a TrackResponse."""
    return TrackResponse(
        video_id=track.video_id,
        title=track.title,
        artist=track.artist,
        artists=track.artists,
        album=track.album,
        duration=track.duration_display,
        thumbnail_url=track.thumbnail_url,
        view_count=track.view_count,
        video_type=getattr(track, "video_type", None),
    )


class SearchResponse(BaseModel):
    """Response body for search endpoint."""

    query: str
    results: list[TrackResponse]


class AlbumResponse(BaseModel):
    """Album data for API responses."""

    browse_id: str
    title: str
    artist: str
    year: int | None
    track_count: int | None
    thumbnail_url: str | None
    audio_playlist_id: str | None = None
    album_type: str | None = None
    is_explicit: bool = False


class AlbumSearchResponse(BaseModel):
    """Response body for album search endpoint."""

    query: str
    results: list[AlbumResponse]


class AlbumTracksResponse(BaseModel):
    """Response body for album tracks endpoint."""

    browse_id: str
    album_title: str
    tracks: list[TrackResponse]


class StreamUrlResponse(BaseModel):
    """Response for stream URL endpoint."""

    video_id: str
    url: str
    expires_in: int
    is_hls: bool = False


class MoodCategoryResponse(BaseModel):
    """A mood/genre category."""

    title: str
    params: str


class MoodSectionResponse(BaseModel):
    """A section of mood/genre categories."""

    title: str
    categories: list[MoodCategoryResponse]


class MoodPlaylistResponse(BaseModel):
    """A playlist from a mood/genre category."""

    playlist_id: str
    title: str
    thumbnail_url: str | None
    author: str | None


class ChartTrackResponse(BaseModel):
    """A chart track."""

    video_id: str
    title: str
    artist: str
    artists: list[str]
    album: str | None
    thumbnail_url: str | None
    rank: str | None
    trend: str | None
    view_count: str | None = None
    duration: str | None = None
    video_type: str | None = None


class ChartArtistResponse(BaseModel):
    """A chart artist."""

    browse_id: str
    title: str
    thumbnail_url: str | None
    rank: str | None
    trend: str | None


class ChartsResponse(BaseModel):
    """Charts response."""

    country: str
    tracks: list[ChartTrackResponse]
    artists: list[ChartArtistResponse]


class LibraryTrackResponse(BaseModel):
    """A local audio file on disk."""

    entry_path: str
    title: str
    artist: str
    album: str | None
    duration: str | None
    file_size: int
    modified_at: float


class LibraryTracksResponse(BaseModel):
    """Response body for the local library endpoint."""

    tracks: list[LibraryTrackResponse]
    total: int
    limit: int
    offset: int


class QueueAddRequest(BaseModel):
    """Request to add a job to the queue."""

    video_id: str
    title: str
    artist: str
    artists: list[str] | None = None
    album: str | None = None
    audio_format: str = "opus"


class QueueAddAlbumRequest(BaseModel):
    """Request to add an album to the queue."""

    browse_id: str
    album_title: str
    artist: str
    album_year: int | None = None
    audio_format: str = "opus"


class QueueAddResponse(BaseModel):
    """Response after adding a job."""

    job_id: str | None = None
    status: str
