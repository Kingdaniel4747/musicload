"""Download job queue models."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class JobStatus(Enum):
    """Download job status states."""

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadJob(BaseModel):
    """Download job with progress tracking."""

    model_config = ConfigDict(use_enum_values=False)

    id: str
    video_id: str
    title: str
    artist: str
    format: str
    status: JobStatus
    artists: Optional[list[str]] = None
    progress: float = 0.0
    speed: str = ""
    eta: str = ""
    error: Optional[str] = None
    file_path: Optional[str] = None
    created_at: datetime = datetime.min
    completed_at: Optional[datetime] = None
    playlist_name: Optional[str] = None
    album: Optional[str] = None
    album_artist: Optional[str] = None
    album_year: Optional[int] = None
    track_number: Optional[int] = None

    def model_post_init(self, __context) -> None:
        """Set created_at to now if not provided (sentinel default)."""
        if self.created_at == datetime.min:
            object.__setattr__(self, "created_at", datetime.now())

    def to_dict(self) -> dict:
        """Convert job to dict for JSON serialization.

        Kept for backward compatibility — prefer model_dump(mode='json').
        """
        return {
            "id": self.id,
            "video_id": self.video_id,
            "title": self.title,
            "artist": self.artist,
            "format": self.format,
            "status": self.status.value,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "error": self.error,
            "file_path": self.file_path,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
