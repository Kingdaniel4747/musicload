"""Pydantic domain models for Musicload.

Canonical import location for all domain models. Original modules
re-export these for backward compatibility.
"""

from musicload.models.deezer import DeezerTrack
from musicload.models.queue import DownloadJob, JobStatus
from musicload.models.search import (
    Album,
    ChartArtist,
    Charts,
    ChartTrack,
    MoodCategory,
    MoodPlaylist,
    MoodSection,
    SongMetadata,
    Track,
)
from musicload.models.unavailable import UnavailableRecord

__all__ = [
    "Album",
    "ChartArtist",
    "Charts",
    "ChartTrack",
    "DeezerTrack",
    "DownloadJob",
    "JobStatus",
    "MoodCategory",
    "MoodPlaylist",
    "MoodSection",
    "SongMetadata",
    "Track",
    "UnavailableRecord",
]
